from re import RegexFlag
import re
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier


class Regionalization_Gradient:
    
    def regionalization_gradient_make(self, df_hex, labels, colors=None, cm=plt.cm.nipy_spectral_r,
                                      max_depth=5, random_state=0, plot=True, n_jobs=-1, **kwargs):
        
        if n_jobs == -1:
            n_jobs = self.cpu_count
            
        #Initiate classifier 
        clf = RandomForestClassifier(max_depth=max_depth, random_state=random_state, n_jobs=n_jobs, **kwargs)
        
        #Make dictionary of original colors
        unique_labels = np.unique(labels)
        color_dict = {}
        if type(colors) == type(None):
            for i, l in enumerate(unique_labels):
                color_dict[l] = cm(i/unique_labels.shape[0])
        else:
            for l in unique_labels:
                index = np.where(labels==l)[0][0]
                color_dict[l] = colors[index]
        
        #Fit       
        clf.fit(df_hex.T, labels)
        
        #Predict labels
        predicted_labels = clf.predict(df_hex.T)
        total = labels.shape[0]
        matches = (labels == predicted_labels).sum()
        percentage = round((matches/total)*100)
        print(f'Perfect matches: {matches} out of {total} which is {percentage}% accuracy')
        if percentage > 98:
            print('Warning, percentage of identical matches is high which might indicate over fitting of the classifier.')
            print('Consider initiating the classifier again with more stringent settings.')
    
        #Get probability of class
        predicted_prob = clf.predict_proba(df_hex.T)
        #Get colors in same order as clasifier class
        region_colors = [color_dict[i] for i in clf.classes_]
        
        #Mix colors based on class probablitiy
        mix_color = []
        for p in predicted_prob:
            weighted_color = p[:,np.newaxis] * region_colors
            new_color = weighted_color.sum(axis=0)
            mix_color.append(new_color[:-1])
            
        if plot:
            
            fig, axes = plt.subplots(figsize=(20,20), ncols=3)
            
            #Plot original labels
            ax0 = axes[0]
            if type(colors) == type(None):
                self.hexbin_plot(labels, cm=cm, ax=ax0)
            else:
                self.hexbin_plot(colors, ax=ax0)
            ax0.set_title('Original Labels', fontsize=14)
            ax0.set_axis_off()
            
            #Plot predicted labels
            ax1=axes[1]
            if type(colors) == type(None):
                self.hexbin_plot(predicted_labels, cm=cm, ax=ax1)
            else:
                #Reuse same colors as original
                colors_new = [color_dict[i] for i in predicted_labels]
                self.hexbin_plot(colors_new, ax=ax1)
            ax1.set_title(f'Predicted Labels, {percentage}% accuracy', fontsize=14)
            ax1.set_axis_off()
            
            ax2 = axes[2]
            self.hexbin_plot(mix_color, ax=ax2)
            ax2.set_title('Mixed Labels', fontsize=14)
            ax2.set_axis_off()
            
        return mix_color, predicted_prob, predicted_labels
            
    
    

    """def __init__(self, max_depth=5, random_state=0, n_jobs=-1, **kwargs):
        
        if n_jobs == -1:
            n_jobs = self.cpu_count
            
        print('initiated RG')
        
        self._regionalization_gradient_clf = RandomForestClassifier(max_depth=max_depth, random_state=random_state,
                                                                    n_jobs=n_jobs, **kwargs)
        
    def _label_color_dict(self, labels, colors=None, cm=plt.cm.nipy_spectral_r):
        
        unique_labels = np.unique(labels)
        color_dict = {}
        
        if type(colors) == type(None):
            for i, l in enumerate(unique_labels):
                color_dict[l] = cm(i/unique_labels.shape[0])

        else:
            for l in unique_labels:
                index = np.where(labels==l)[0][0]
                color_dict[l] = colors[index]    
            
    def fit(self, df_hex, labels, colors=None, cm=plt.cm.nipy_spectral_r, sample_weight=None, plot=True):
        
        self._regionalization_gradient_clf.fit(df_hex.T, labels, sample_weight)
        
        #Predict labels
        predicted_labels = self._regionalization_gradient_clf.predict(df_hex.T)
        total = labels.shape[0]
        matches = (labels == predicted_labels).sum()
        percentage = round((matches/total)*100)
        print(f'Perfect matches: {matches} out of {total} which is {percentage}%')
        if percentage > 95:
            print('Warning, percentage of identical matches is high which might indicate over fitting of the classifier.')
            print('Consider initiating the classifier again with more stringent settings.')
            
        #Save colors
        self._classifier_color_dict = self._label_color_dict(labels, colors=colors, cm=cm)
        
        if plot:
            fig, axes = plt.subplots(figsize=(10,10), ncols=2)
            
            #Plot original labels
            ax0 = axes[0]
            if type(colors) == type(None):
                self.hexbin_plot(labels, cm=cm, ax=ax0)
            else:
                self.hexbin_plot(labels, c=colors, ax=ax0)
            ax0.set_title('Original Labels', fontsize=14)
            ax0.set_axis_off()
            
            #Plot predicted labels
            ax1=axes[1]
            if type(colors) == type(None):
                self.hexbin_plot(predicted_labels, cm=cm, ax=ax1)
            else:
                #Reuse same colors as original
                colors_new = [self._classifier_color_dict[i] for i in predicted_labels]
                self.hexbin_plot(predicted_labels, c=colors_new, ax=ax1)
                ax1.set_title('Predicted Labels', fontsize=14)
                ax1.set_axis_off()
                
    def predict(self, df_hex, plot=True):
        
        #Get probability of class
        predicted_prob = self._regionalization_gradient_clf.predict_proba(df_hex.T)
        #Get colors in same order as clasifier class
        region_colors = [self._classifier_color_dict[i] for i in self._regionalization_gradient_clf.classes_]
        
        #Mix colors based on class probablitiy
        mix_color = []
        for p in predicted_prob:
            weighted_color = p[:,np.newaxis] * region_colors
            new_color = weighted_color.sum(axis=0)
            mix_color.append(new_color[:-1])
            
        #Plot results
        if plot:
            fig, ax = plt.subplots(figsize=(10,10))
            self.hexbin_plot(mix_color, ax=ax)
            ax.set_title('Mixed Labels', fontsize=14)
            ax.set_axis_off()
        
        return mix_color, predicted_prob
        
        
class Regionalization_Gradient:
    
    def __init__():
        return RG"""