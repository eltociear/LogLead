import polars as pl
import numpy as np
from collections import Counter
#Faster sklearn enabled. See https://intel.github.io/scikit-learn-intelex/latest/
# Causes problems in RandomForrest. We have to use older version due to tensorflow numpy combatibilities
# from sklearnex import patch_sklearn
#patch_sklearn()
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.tree import DecisionTreeClassifier
from sklearn.svm import LinearSVC
from sklearn.ensemble import IsolationForest
from sklearn.neighbors import LocalOutlierFactor
from sklearn.cluster import KMeans
from sklearn.ensemble import RandomForestClassifier
from xgboost import XGBClassifier
from sklearn.svm import OneClassSVM
from sklearn.metrics import f1_score
from sklearn.metrics import confusion_matrix
import csv
from io import StringIO
import pandas as pd


from sklearn.metrics import accuracy_score
from scipy.sparse import hstack
import scipy.sparse
import math
import time

from scipy.sparse import csr_matrix
import matplotlib.pyplot as plt
from sklearn.metrics import roc_curve, auc
from sklearn.feature_extraction.text import TfidfVectorizer

class AnomalyDetection:
    def __init__(self, item_list_col=None, numeric_cols=None, emb_list_col=None, label_col="anomaly", 
                 store_scores=False, print_scores=True):
        self.item_list_col = item_list_col
        self.numeric_cols = numeric_cols if numeric_cols else []
        self.label_col = label_col
        self.emb_list_col = emb_list_col
        self.store_scores = store_scores
        self.storage = ModelResultsStorage()
        self.print_scores=print_scores
        self.train_vocabulary = None

        
    def test_train_split(self, df, test_frac=0.9, vec_name="CountVectorizer", oov_analysis=False):
        # Shuffle the DataFrame
        df = df.sample(fraction = 1.0, shuffle=True)
        # Split ratio
        test_size = int(test_frac * df.shape[0])

        # Split the DataFrame using head and tail
        self.test_df = df.head(test_size)
        self.train_df = df.tail(-test_size)
        self.prepare_train_test_data(vec_name=vec_name, oov_analysis=oov_analysis)
        
    def prepare_train_test_data(self, vec_name="CountVectorizer", oov_analysis=False):
        #Prepare all data for running
        self.X_train, self.labels_train = self._prepare_data(True, self.train_df, vec_name, oov_analysis)
        self.X_test, self.labels_test = self._prepare_data(False, self.test_df,vec_name, oov_analysis)
        #No anomalies dataset is used for some unsupervised algos. 
        self.X_train_no_anos, _ = self._prepare_data(True, self.train_df.filter(pl.col(self.label_col).not_()), vec_name, oov_analysis)
        self.X_test_no_anos, self.labels_test_no_anos = self._prepare_data(False, self.test_df, vec_name, oov_analysis)
     
        
    def _prepare_data(self, train, df_seq, vec_name, oov_analysis=False):
        X = None
        labels = df_seq.select(pl.col(self.label_col)).to_series().to_list()

        # Extract events
        if self.item_list_col:
            # Extract the column
            column_data = df_seq.select(pl.col(self.item_list_col))             
            events = column_data.to_series().to_list()
            vectorizer_class = globals()[vec_name]
            # We are training
            if train:
                # Check the datatype  
                if column_data.dtypes[0]  == pl.datatypes.Utf8: #We get strs -> Use SKlearn Tokenizer
                    self.vectorizer = vectorizer_class() 
                elif column_data.dtypes[0]  == pl.datatypes.List(pl.datatypes.Utf8): #We get list of str, e.g. words -> Do not use Skelearn Tokinizer 
                    self.vectorizer = vectorizer_class(analyzer=lambda x: x)
                X = self.vectorizer.fit_transform(events)
                self.train_vocabulary = self.vectorizer.vocabulary_

            # We are predicting
            else:
                X = self.vectorizer.transform(events)
                if oov_analysis:
                    oov_vectorizer = CountVectorizer(analyzer=lambda x: x, binary=True)
                    X_binary = oov_vectorizer.fit_transform(events)
                    feature_names = oov_vectorizer.get_feature_names_out()
                    oov_terms = set(feature_names) - set(self.train_vocabulary)
                    oov_indicator = np.array([feature in oov_terms for feature in feature_names], dtype=int)
                    oov_indicator_sparse = csr_matrix(oov_indicator)
                    oov_counts = X_binary.dot(oov_indicator_sparse.T)
                    oov_counts = oov_counts.toarray().ravel()
                    self.test_df = self.test_df.with_columns(pl.Series(name="oov_count", values=oov_counts))
                    

        # Extract lists of embeddings
        if  self.emb_list_col:
            emb_list = df_seq.select(pl.col(self.emb_list_col)).to_series().to_list()
            
            # Convert lists of floats to a matrix
            #emb_matrix = np.array(emb_list)
            emb_matrix = np.vstack(emb_list)
            # Stack with X
            X = hstack([X, emb_matrix]) if X is not None else emb_matrix

        # Extract additional predictors
        if self.numeric_cols:
            additional_features = df_seq.select(self.numeric_cols).to_pandas().values
            X = hstack([X, additional_features]) if X is not None else additional_features

        return X, labels    
        

         
    def train_model(self, model, filter_anos=False):
        X_train_to_use = self.X_train_no_anos if filter_anos else self.X_train
        #Store the current the model and whether it uses ano data or no
        self.model = model
        self.filter_anos = filter_anos
        self.model.fit(X_train_to_use, self.labels_train)

    #def dep_train_model(self, df_seq, model):
    #    X_train, labels = self._prepare_data(train=True, df_seq=df_seq)
    #    self.model = model
    #    self.model.fit(X_train, labels)
    
    def predict(self, custom_plot=False):
        #X_test, labels = self._prepare_data(train=False, df_seq=df_seq)
        X_test_to_use = self.X_test_no_anos if self.filter_anos else self.X_test
        predictions = self.model.predict(X_test_to_use)
        #Unsupervised modeles give predictions between -1 and 1. Convert to 0 and 1
        if isinstance(self.model, (IsolationForest, LocalOutlierFactor,KMeans, OneClassSVM)):
            predictions = np.where(predictions < 0, 1, 0)
        df_seq = self.test_df.with_columns(pl.Series(name="pred_normal", values=predictions.tolist()))
        if self.print_scores:
            self._print_evaluation_scores(self.labels_test, predictions, self.model)
        if custom_plot:
            self.model.custom_plot(self.labels_test)
        if self.store_scores:
            self.storage.store_test_results(self.labels_test, predictions, self.model, 
                                            self.item_list_col, self.numeric_cols, self.emb_list_col)
        return df_seq 
       
    def train_LR(self, max_iter=1000):
        self.train_model (LogisticRegression(max_iter=max_iter))
    
    def train_DT(self):
        self.train_model (DecisionTreeClassifier())

    def train_LSVM(self, penalty='l1', tol=0.1, C=1, dual=False, class_weight=None, max_iter=1000):
        self.train_model (LinearSVC(
            penalty=penalty, tol=tol, C=C, dual=dual, class_weight=class_weight, max_iter=max_iter))

    def train_IsolationForest(self, n_estimators=100,  max_samples='auto', contamination="auto",filter_anos=False):
        self.train_model (IsolationForest(
            n_estimators=n_estimators, max_samples=max_samples, contamination=contamination), filter_anos=filter_anos)
                          
    def train_LOF(self, n_neighbors=20, max_samples='auto', contamination="auto", filter_anos=True):
        #LOF novelty=True model needs to be trained without anomalies
        #If we set novelty=False then Predict is no longer available for calling.
        #It messes up our general model prediction routine
        self.train_model (LocalOutlierFactor(
            n_neighbors=n_neighbors,  contamination=contamination, novelty=True), filter_anos=filter_anos)
    
    def train_KMeans(self):
        self.train_model(KMeans(n_init="auto",n_clusters=2))

    def train_OneClassSVM(self):
        self.train_model(OneClassSVM(max_iter=1000))

    def train_RF(self):
        self.train_model( RandomForestClassifier())

    def train_XGB(self):
        self.train_model(XGBClassifier())

    def evaluate_all_ads(self, disabled_methods=[]):
        for method_name in sorted(dir(self)): 
            if (method_name.startswith("train_") 
                and not method_name.startswith("train_model") 
                and method_name not in disabled_methods):
                method = getattr(self, method_name)
                if callable(method):
                    if not self.print_scores:
                        print (f"Running {method_name}")
                    time_start = time.time()
                    method()
                    self.predict()
                    if self.print_scores:
                        print(f'Total time: {time.time()-time_start:.2f} seconds')
        if self.print_scores:
            print("---------------------------------------------------------------")


    def _print_evaluation_scores(self, y_test, y_pred, model, f_importance = False, auc_roc = False):
        print(f"Results from model: {type(model).__name__}")
        # Evaluate the model's performance
        accuracy = accuracy_score(y_test, y_pred)
        print(f"Accuracy: {accuracy:.4f}")
        

        # Compute the F1 score
        f1 = f1_score(y_test, y_pred)
        # Print the F1 score
        print(f"F1 Score: {f1:.4f}")



        #import matplotlib.pyplot as plt
        #import seaborn as sns
        cm = confusion_matrix(y_test, y_pred)
        # Print the confusion matrix
        print("Confusion Matrix:")
        print(cm)
        #F1--------------------------------------------------------

        # Print feature importance
        if (f_importance):
            if hasattr(self, 'vectorizer') and self.vectorizer:
                event_features = self.vectorizer.get_feature_names_out()
                event_features = list(event_features)
            else:
                event_features = []

            all_features = event_features + self.numeric_cols
            if isinstance(model, (LogisticRegression, LinearSVC)):
                feature_importance = abs(model.coef_[0])
            elif isinstance(model, DecisionTreeClassifier):
                feature_importance = model.feature_importances_
            else:
                print("Model type not supported for feature importance extraction")
                return
            sorted_idx = feature_importance.argsort()[::-1]  # Sort in descending order

            print("\nTop Important Predictors:")
            for i in range(min(10, len(sorted_idx))):  # Print top 10 or fewer
                print(f"{all_features[sorted_idx[i]]}: {feature_importance[sorted_idx[i]]:.4f}")
                
        #AUC-ROC analysis for selected unsupervised models
        if auc_roc:      
            titlestr = type(self.model).__name__ + " ROC"
            X_test_to_use = self.X_test_no_anos if self.filter_anos else self.X_test
            if isinstance(self.model, IsolationForest):
                y_pred = 1 - model.score_samples(X_test_to_use) #lower = anomalous
                auc_roc_analysis(y_test, y_pred, titlestr)
            if isinstance(self.model, KMeans):
                y_pred = np.min(model.transform(X_test_to_use), axis=1) #Shortest distance from the cluster to be used as ano score
                auc_roc_analysis(y_test, y_pred, titlestr)



class ModelResultsStorage:
    def __init__(self):
        self.test_results = []

    def _create_input_signature(self, item_list_col, numeric_cols, emb_list_col):
        # Create the input signature by concatenating all input types
        input_parts = (
            item_list_col if item_list_col is not None else [],
            numeric_cols if numeric_cols is not None else [],
            emb_list_col if emb_list_col is not None else [],
        )
        input_signature = ''.join(str(item) for sublist in input_parts for item in sublist)
        return input_signature

    def store_test_results(self, y_test, y_pred, model, item_list_col=None, numeric_cols=None, emb_list_col=None):
        input_signature = self._create_input_signature(item_list_col, numeric_cols, emb_list_col)

        result = {
            'model': model,
            'y_test': y_test,
            'y_pred': y_pred,
            'input_signature': input_signature,
        }
        self.test_results.append(result)

    def calculate_average_scores(self, score_type='accuracy'):
        if score_type not in ['accuracy', 'f1']:
            raise ValueError("score_type must be 'accuracy' or 'f1'.")

        # Create a list to store each row of the DataFrame
        data_for_df = []

        # Iterate over stored results and calculate scores
        for result in self.test_results:
            model_name = type(result['model']).__name__
            input_signature = result['input_signature']
            
            # Calculate scores
            acc = accuracy_score(result['y_test'], result['y_pred'])
            f1 = f1_score(result['y_test'], result['y_pred'])
            
            # Append row to the list
            data_for_df.append({
                'Model': model_name,
                'Input Signature': input_signature,
                'Accuracy': acc,
                'F1 Score': f1
            })

        # Convert the list to a DataFrame
        df = pd.DataFrame(data_for_df)

        # Calculate average scores
        if score_type == 'accuracy':
            df_average = df.groupby(['Model', 'Input Signature']).agg({'Accuracy': 'mean'}).reset_index()
        else:
            df_average = df.groupby(['Model', 'Input Signature']).agg({'F1 Score': 'mean'}).reset_index()

        # Pivot the DataFrame to get Input Signatures as columns
        score_column = 'Accuracy' if score_type == 'accuracy' else 'F1 Score'
        df_pivot = df_average.pivot(index='Model', columns='Input Signature', values=score_column).reset_index()


        # Pivot the DataFrame to get Input Signatures as columns
        #df_pivot = df_average.pivot(index='Model', columns='Input Signature', values=score_type.capitalize()).reset_index()

        # Fill NaN values with 0 or an appropriate value
        df_pivot.fillna(0, inplace=True)

        return df_pivot

    def print_confusion_matrices(self, model_filter=None, signature_filter=None):
        for result in self.test_results:
            model_name = type(result['model']).__name__
            input_signature = result['input_signature']
            cm = confusion_matrix(result['y_test'], result['y_pred'])

            # Check if model_name matches model_filter, if provided
            if model_filter and model_name != model_filter:
                continue

            # Check if input_signature matches signature_filter, if provided
            if signature_filter and input_signature != signature_filter:
                continue

            print(f"Model: {model_name}")
            print(f"Input Signature: {input_signature}")
            print("Confusion Matrix:")
            print(cm)
            

    def print_scores(self, model_filter=None, signature_filter=None, score_type='all'):
        # Check for valid score_type
        valid_score_types = ['accuracy', 'f1', 'all']
        if score_type not in valid_score_types:
            raise ValueError(f"score_type must be one of {valid_score_types}")

        for result in self.test_results:
            model_name = type(result['model']).__name__
            input_signature = result['input_signature']
            acc = accuracy_score(result['y_test'], result['y_pred'])
            f1 = f1_score(result['y_test'], result['y_pred'])

            # Filter results
            if model_filter and model_name != model_filter:
                continue
            if signature_filter and input_signature != signature_filter:
                continue

            # Print results based on score_type
            print(f"Model: {model_name}")
            print(f"Input Signature: {input_signature}")
            if score_type in ['accuracy', 'all']:
                print(f"Accuracy: {acc:.4f}")
            if score_type in ['f1', 'all']:
                print(f"F1 Score: {f1:.4f}")



def auc_roc_analysis(labels, preds, titlestr = "ROC", plot=True):
    # Compute the ROC curve
    fpr, tpr, thresholds = roc_curve(labels, preds)
    # Compute the AUC from the points of the ROC curve
    roc_auc = auc(fpr, tpr)

    if plot:
        # Plot the ROC curve
        plt.figure()
        lw = 2
        plt.plot(fpr, tpr, color='darkorange', lw=lw, label='ROC curve (area = %0.2f)' % roc_auc)
        plt.plot([0, 1], [0, 1], color='navy', lw=lw, linestyle='--')
        plt.xlim([0.0, 1.0])
        plt.ylim([0.0, 1.05])
        plt.xlabel('False Positive Rate')
        plt.ylabel('True Positive Rate')
        plt.title(titlestr)
        plt.legend(loc="lower right")
        plt.show()

    return roc_auc

    
def test_train_split(df, test_frac):
    # Shuffle the DataFrame
    df = df.sample(fraction = 1.0, shuffle=True)
    # Split ratio
    test_size = int(test_frac * df.shape[0])

    # Split the DataFrame using head and tail
    test_df = df.head(test_size)
    train_df = df.tail(-test_size)
    return train_df, test_df
