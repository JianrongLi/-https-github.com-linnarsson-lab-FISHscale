import re
from threading import local
from dgl.convert import graph
from numpy.random import poisson
import torchmetrics
import dgl
import torch as th
import torch.nn as nn
import torch.nn.functional as F
import dgl.nn.pytorch as dglnn
import dgl.function as fn
import tqdm
from pytorch_lightning import LightningModule
from FISHscale.graphNN.submodules import Classifier, PairNorm, DiffGroupNorm,NegativeBinomial
from torch.distributions import Gamma,Normal, Multinomial, kl_divergence as kl

class CrossEntropyLoss(nn.Module):
    def forward(self, block_outputs, pos_graph, neg_graph):
        with pos_graph.local_scope():
            pos_graph.ndata['h'] = block_outputs
            pos_graph.apply_edges(fn.u_mul_v('h', 'h', 'score'))
            pos_score = pos_graph.edata['score']
        with neg_graph.local_scope():
            neg_graph.ndata['h'] = block_outputs
            neg_graph.apply_edges(fn.u_mul_v('h', 'h', 'score'))
            neg_score = neg_graph.edata['score']
        
        pos_loss, neg_loss=  -F.logsigmoid(pos_score.sum(-1)).mean(), - F.logsigmoid(-neg_score.sum(-1)).mean()
        loss = pos_loss + neg_loss
        #score = th.cat([pos_score, neg_score])
        #label = th.cat([th.ones_like(pos_score), th.zeros_like(neg_score)]).long()
        #loss = F.binary_cross_entropy_with_logits(score, label.float())
        return loss, pos_loss, neg_loss

class SAGELightning(LightningModule):
    def __init__(self,
                 in_feats,
                 n_hidden,
                 n_classes,
                 n_layers,
                 activation=F.relu,
                 dropout=0.2,
                 lr=0.001,
                 supervised=False,
                 reference=0,
                 smooth=False,
                 device='cpu',
                 aggregator='attentional',
                 celltype_distribution=None,
                 ):
        super().__init__()

        self.save_hyperparameters()
        self.module = SAGE(in_feats, n_hidden, n_classes, n_layers, activation, dropout, supervised,aggregator)
        self.lr = lr
        self.supervised= supervised
        self.loss_fcn = CrossEntropyLoss()
        self.kappa = 0
        self.reference=th.tensor(reference,dtype=th.float32)
        self.smooth = smooth
        if self.supervised:
            #self.automatic_optimization = False
            self.train_acc = torchmetrics.Accuracy()
            self.kl = th.nn.KLDivLoss(reduction='sum')
            self.dist = celltype_distribution

    def training_step(self, batch, batch_idx):
        batch1 = batch#['unlabelled']
        self.reference = self.reference.to(self.device)
        _, pos_graph, neg_graph, mfgs = batch1
        mfgs = [mfg.int() for mfg in mfgs]
        batch_inputs_u = mfgs[0].srcdata['gene']
        batch_pred_unlab = self.module(mfgs, batch_inputs_u)
        bu = batch_inputs_u[pos_graph.nodes()]
        graph_loss,pos, neg = self.loss_fcn(batch_pred_unlab, pos_graph, neg_graph)
        
        if self.supervised:
            probabilities_unlab = F.softmax(self.module.encoder.encoder_dict['CF'](batch_pred_unlab[pos_graph.nodes()]),dim=-1)
            predictions = probabilities_unlab.argsort(axis=-1)[:,-1]
            local_nghs = mfgs[0].srcdata['ngh'][pos_graph.nodes()]
            mu = probabilities_unlab @ self.reference
            alpha = 1/local_nghs.mean(axis=0).pow(2)
            rate = alpha/mu

            NB = NegativeBinomial(concentration=alpha,rate=rate)#.log_prob(local_nghs).mean(axis=-1).mean()
            nb_loss = NB.log_prob(local_nghs).mean(axis=-1).mean()
            # Introduce reference with sampling
            # Regularize by local nodes
            #bone_fight_loss = -F.cosine_similarity(probabilities_unlab @ self.reference.T.to(self.device), local_nghs,dim=1).mean()
            #bone_fight_loss = -F.cosine_similarity(probabilities_unlab @ self.reference.T.to(self.device), local_nghs,dim=0).mean()
            # Add Predicted same class nodes together.
            loss = graph_loss + nb_loss
            self.log('train_loss', loss, prog_bar=True, on_step=True, on_epoch=True)
            self.log('nb_loss', nb_loss, prog_bar=True, on_step=True, on_epoch=True)
            self.log('Graph Loss', graph_loss, prog_bar=False, on_step=True, on_epoch=False)

        else:
            loss = graph_loss
            self.log('train_loss', loss, prog_bar=True, on_step=True, on_epoch=True)
        
        return loss

    def validation_step(self, batch, batch_idx):
        input_nodes, output_nodes, mfgs = batch
        mfgs = [mfg.int() for mfg in mfgs]
        batch_inputs = mfgs[0].srcdata['gene']
        batch_pred = self.module(mfgs, batch_inputs)
        return batch_pred

    def configure_optimizers(self):
        optimizer = th.optim.Adam(self.module.parameters(), lr=self.lr)
        return optimizer


class SAGE(nn.Module):
    '''def __init__(self, in_feats, n_hidden, n_classes, n_layers, activation, dropout):
        super().__init__()
        self.init(in_feats, n_hidden, n_classes, n_layers, activation, dropout)'''

    def __init__(self, 
                in_feats, 
                n_hidden, 
                n_classes, 
                n_layers, 
                activation, 
                dropout,
                supervised,
                aggregator):
        super().__init__()
        self.n_layers = n_layers
        self.n_hidden = n_hidden
        self.n_classes = n_classes
        self.supervised = supervised
        self.aggregator = aggregator
        if self.supervised:
            self.domain_adaptation = Classifier(n_input=n_hidden,
                                                n_labels=2,
                                                softmax=False,
                                                reverse_gradients=True)

        self.encoder = Encoder(in_feats,
                                n_hidden,
                                n_classes,
                                n_layers,
                                supervised,
                                aggregator)
        self.mean_encoder = self.encoder.encoder_dict['mean']
        self.var_encoder = self.encoder.encoder_dict['var']

    def forward(self, blocks, x):
        h = th.log(x+1)   
        for l, (layer, block) in enumerate(zip(self.encoder.encoder_dict['GS'], blocks)):
            feat_n = []
            if self.aggregator != 'attentional':
                h = layer(block, h,)#.mean(1)
                #h = self.encoder.encoder_dict['FC'][l](h)
            else:
                h = layer(block, h,).mean(1)
                #h = self.encoder.encoder_dict['FC'][l](h)
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
        self.eval()
        for l, layer in enumerate(self.encoder.encoder_dict['GS']):
            if l ==  0:
                y = th.zeros(g.num_nodes(), self.n_hidden) #if not self.supervised else th.zeros(g.num_nodes(), self.n_classes)
            else: 
                y = th.zeros(g.num_nodes(), self.n_hidden)

            sampler = dgl.dataloading.MultiLayerFullNeighborSampler(1)
            dataloader = dgl.dataloading.NodeDataLoader(
                g,
                th.arange(g.num_nodes()),#.to(g.device),
                sampler,
                batch_size=batch_size,
                shuffle=False,
                drop_last=False,
                num_workers=num_workers)

            for input_nodes, output_nodes, blocks in tqdm.tqdm(dataloader):
                block = blocks[0]
                block = block.int()
                if l == 0:
                    h = th.log(x[input_nodes]+1)#.to(device)
                else:
                    h = x[input_nodes]

                if self.aggregator != 'attentional':
                    h = layer(block, h,)
                else:
                    h = layer(block, h,).mean(1)
                    #h = self.encoder.encoder_dict['FC'][l](h)

                #if l == 1:
                #    h = self.mean_encoder(h)#, th.exp(self.var_encoder(h))+1e-4 )
                y[output_nodes] = h.cpu().detach()#.numpy()
            x = y
    
        return y

class Encoder(nn.Module):
        def __init__(
            self,
            in_feats,
            n_hidden,
            n_classes,
            n_layers,
            supervised,
            aggregator,
            ):
            super().__init__()
        

            self.mean_encoder = nn.Linear(n_hidden, n_hidden)
            self.var_encoder = nn.Linear(n_hidden, n_hidden)
            layers = nn.ModuleList()

            if supervised:
                classifier = Classifier(n_input=n_hidden,
                                        n_labels=n_classes,
                                        softmax=False,
                                        reverse_gradients=False)
            else:
                classifier = None

            if supervised:
                self.norm = F.normalize#DiffGroupNorm(n_hidden,n_classes,None) 
            else:
                self.norm = F.normalize#DiffGroupNorm(n_hidden,20) 

            for i in range(0,n_layers-1):
                if i > 0:
                    in_feats = n_hidden
                    x = 0.2
                else:
                    x = 0

                if aggregator == 'attentional':
                    layers.append(dglnn.GATConv(in_feats, 
                                                n_hidden, 
                                                num_heads=4,
                                                feat_drop=x,
                                                activation=F.relu,
                                                norm=self.norm,
                                                #allow_zero_in_degree=False
                                                ))

                else:
                    layers.append(dglnn.SAGEConv(in_feats, 
                                                n_hidden, 
                                                aggregator_type=aggregator,
                                                #feat_drop=0.2,
                                                activation=F.relu,
                                                norm=self.norm,
                                                ))

            if aggregator == 'attentional':
                layers.append(dglnn.GATConv(n_hidden, 
                                            n_hidden, 
                                            num_heads=4, 
                                            feat_drop=0.2,
                                            #activation=F.relu,
                                            #allow_zero_in_degree=False
                                            ))

            else:
                layers.append(dglnn.SAGEConv(n_hidden, 
                                                n_hidden, 
                                                aggregator_type=aggregator,
                                                feat_drop=0.2,
                                                #activation=F.relu,
                                                #norm=F.normalize
                                                ))

            self.encoder_dict = nn.ModuleDict({'GS': layers, 
                                                'mean': self.mean_encoder,
                                                'var':self.var_encoder,
                                                'CF':classifier})