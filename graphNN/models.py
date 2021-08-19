import pytorch_lightning as pl
import torchmetrics
import math
import numpy as np
import dgl
import numpy as np
import torch as th
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
import dgl.nn.pytorch as dglnn
import dgl.function as fn
import tqdm
from pytorch_lightning.metrics import Accuracy
from pytorch_lightning.callbacks import ModelCheckpoint, Callback
from pytorch_lightning import LightningDataModule, LightningModule, Trainer

class CrossEntropyLoss(nn.Module):
    def forward(self, block_outputs, pos_graph, neg_graph):
        with pos_graph.local_scope():
            pos_graph.ndata['h'] = block_outputs
            pos_graph.apply_edges(fn.u_dot_v('h', 'h', 'score'))
            pos_score = pos_graph.edata['score']
        with neg_graph.local_scope():
            neg_graph.ndata['h'] = block_outputs
            neg_graph.apply_edges(fn.u_dot_v('h', 'h', 'score'))
            neg_score = neg_graph.edata['score']

        score = th.cat([pos_score, neg_score])
        label = th.cat([th.ones_like(pos_score), th.zeros_like(neg_score)]).long()
        loss = F.binary_cross_entropy_with_logits(score, label.float())
        return loss

class SAGELightning(LightningModule):
    def __init__(self,
                 in_feats,
                 n_hidden,
                 n_classes,
                 n_layers,
                 activation=F.relu,
                 dropout=0.2,
                 lr=0.01,
                 ):
        super().__init__()
        self.save_hyperparameters()
        self.module = SAGE(in_feats, n_hidden, n_classes, n_layers, activation, dropout)
        self.lr = lr
        self.loss_fcn = CrossEntropyLoss()

    def training_step(self, batch, batch_idx):
        input_nodes, pos_graph, neg_graph, mfgs = batch
        mfgs = [mfg.int() for mfg in mfgs]
        pos_graph = pos_graph#.to(self.device)
        neg_graph = neg_graph#.to(self.device)
        batch_inputs = mfgs[0].srcdata['gene']

        #batch_labels = mfgs[-1].dstdata['labels']
        batch_pred = self.module(mfgs, batch_inputs)
        loss = self.loss_fcn(batch_pred, pos_graph, neg_graph)
        self.log('train_loss', loss, prog_bar=True, on_step=False, on_epoch=True)
        return loss

    def validation_step(self, batch, batch_idx):
        input_nodes, output_nodes, mfgs = batch
        mfgs = [mfg.int() for mfg in mfgs]
        batch_inputs = mfgs[0].srcdata['gene']
        #batch_labels = mfgs[-1].dstdata['labels']
        batch_pred = self.module(mfgs, batch_inputs)
        return batch_pred

    def configure_optimizers(self):
        optimizer = th.optim.Adam(self.parameters(), lr=self.lr)
        return optimizer

class SAGE(nn.Module):
    def __init__(self, in_feats, n_hidden, n_classes, n_layers, activation, dropout):
        super().__init__()
        self.init(in_feats, n_hidden, n_classes, n_layers, activation, dropout)

    def init(self, in_feats, n_hidden, n_classes, n_layers, activation, dropout):
        self.n_layers = n_layers
        self.n_hidden = n_hidden
        self.n_classes = n_classes
        self.layers = nn.ModuleList()

        self.bns = nn.ModuleList()
        for i in range(n_layers - 1):
            if i == n_layers-1:
                self.bns.append(nn.BatchNorm1d(n_classes))    
            self.bns.append(nn.BatchNorm1d(n_hidden))

        
        '''self.last_layer = nn.Sequential(
                            nn.Linear(n_hidden , n_hidden, bias=True),
                            nn.BatchNorm1d(n_hidden),
                            nn.ReLU())'''

        if n_layers > 1:
            self.layers.append(dglnn.SAGEConv(in_feats, n_hidden, 'pool'))
            for i in range(1, n_layers - 1):
                self.layers.append(dglnn.SAGEConv(n_hidden, n_hidden, 'lstm'))
            self.layers.append(dglnn.SAGEConv(n_hidden, n_classes, 'lstm'))
        else:
            self.layers.append(dglnn.SAGEConv(in_feats, n_classes, 'pool'))
        self.dropout = nn.Dropout(dropout)
        self.activation = activation
        
    def forward(self, blocks, x):
        h = th.log(x+1)
        
        for l, (layer, block) in enumerate(zip(self.layers, blocks)):
            #print(l)
            h = layer(block, h)

            if l != len(self.layers) - 1: #and l != len(self.layers) - 2:
                h = self.bns[l](h)
                h = self.activation(h)
                h = self.dropout(h)
            '''         
            elif l == len(self.layers) - 2:
                h = self.last_layer(h)
            '''

        return h

    def inference(self, g, x, device, batch_size, num_workers):
        """
        Inference with the GraphSAGE model on full neighbors (i.e. without neighbor sampling).
        g : the entire graph.
        x : the input of entire node set.
        The inference code is written in a fashion that it could handle any number of nodes and
        layers.
        """
        # During inference with sampling, multi-layer blocks are very inefficient because
        # lots of computations in the first few layers are repeated.
        # Therefore, we compute the representation of all nodes layer by layer.  The nodes
        # on each layer are of course splitted in batches.
        # TODO: can we standardize this?
        for l, layer in enumerate(self.layers[:-1]):
            y = th.zeros(g.num_nodes(), self.n_hidden if l != len(self.layers) - 1 else self.n_classes)

            sampler = dgl.dataloading.MultiLayerFullNeighborSampler(1)
            dataloader = dgl.dataloading.NodeDataLoader(
                g,
                th.arange(g.num_nodes()).to(g.device),
                sampler,
                batch_size=batch_size,
                shuffle=False,
                drop_last=False,
                num_workers=num_workers)

            for input_nodes, output_nodes, blocks in tqdm.tqdm(dataloader):
                block = blocks[0]
                block = block.int().to(device)
                h = x[input_nodes].to(device)
                h = layer(block, h)

                if l != len(self.layers) - 1: #and l != len(self.layers) - 2:
                    h = self.bns[l](h)
                    h = self.activation(h)
                    h = self.dropout(h)
                '''elif l == len(self.layers) - 2:
                    h = self.last_layer(h)'''
                y[output_nodes] = h.cpu()

            x = y
        return y


# Decoder
class DecoderSCVI(nn.Module):
    def __init__(
        self,
        n_input: int,
        n_output: int,
        n_hidden: int = 48,
        use_batch_norm: bool=True,
        use_relu:bool=True,
        dropout_rate: float=0.1,
        bias: bool=True,
        softmax:bool = True,
    ):
        super().__init__()

        self.px_decoder = nn.Sequential(
                            nn.Linear(n_input , n_hidden, bias=bias),
                            nn.BatchNorm1d(n_hidden, momentum=0.01, eps=0.001) if use_batch_norm else None,
                            nn.ReLU() if use_relu else None,
                            nn.Dropout(p=dropout_rate) if dropout_rate > 0 else None)

        if softmax:
            self.px_scale_decoder = nn.Sequential(nn.Linear(n_hidden, n_output))
        else:
            self.px_scale_decoder = nn.Sequential(nn.Linear(n_hidden, n_output))

    def forward(
        self, z: th.Tensor
    ):
        # The decoder returns values for the parameters of the ZINB distribution
        px = self.px_decoder(z)
        px= F.log_softmax(self.px_scale_decoder(px),dim=-1)
        return px

class Classifier(nn.Module):
    """Basic fully-connected NN classifier
    """

    def __init__(
        self,
        n_input,
        n_hidden=24,
        n_labels=5,
        n_layers=1,
        dropout_rate=0.1,
        softmax=False,
        use_batch_norm: bool=True,
        bias: bool=True,
        use_relu:bool=True,
    ):
        super().__init__()
        layers = [nn.Sequential(
                            nn.Linear(n_input , n_hidden, bias=bias),
                            nn.BatchNorm1d(n_hidden, momentum=0.01, eps=0.001) if use_batch_norm else None,
                            nn.ReLU() if use_relu else None,
                            nn.Dropout(p=dropout_rate) if dropout_rate > 0 else None),
            nn.Linear(n_hidden, n_labels),]

        if softmax:
            layers.append(nn.Softmax(dim=-1))

        self.classifier = nn.Sequential(*layers,nn.ReLU())

    def forward(self, x):
        return F.log_softmax(self.classifier(x),dim=-1)
