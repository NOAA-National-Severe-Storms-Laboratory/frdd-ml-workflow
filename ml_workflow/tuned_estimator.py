# A custom sklearn-based estimator that will build 
# a pre-processing pipeline, perform hyperparamter optimization, 
# and calibration of the final model based on the user inputs. 

from sklearnex import patch_sklearn
patch_sklearn()

import numpy as np
import pandas as pd
import joblib

# sklearn 
from sklearn.model_selection import GroupKFold, KFold
from sklearn.base import (BaseEstimator, ClassifierMixin, RegressorMixin, clone,
                   MetaEstimatorMixin)
from sklearn.utils.validation import check_is_fitted, check_consistent_length
from sklearn.utils import check_X_y, check_array, indexable, column_or_1d

from sklearn.preprocessing import label_binarize, LabelBinarizer, LabelEncoder
from sklearn.utils.validation import check_is_fitted, check_consistent_length
from sklearn.utils.validation import _check_sample_weight
from sklearn.model_selection import check_cv
from sklearn.metrics import average_precision_score 
from sklearn.calibration import CalibratedClassifierCV

# Internal methods 
from .ml_methods import norm_aupdc, norm_csi
from .preprocess.preprocess import PreProcessPipeline
from .hyperparameter_optimizer import HyperOptCV

def scorer(estimator, X, y):
    pred = estimator.predict_proba(X)[:,1]
    return 1.0 - average_precision_score(y, pred)

def dates_to_groups(dates, n_splits=5): 
    """Separated different dates into a set of groups based on n_splits"""
    unique_dates = np.unique(dates.values)
    np.random.shuffle(unique_dates)

    df['groups'] = np.zeros(len(dates))
    for i, group in enumerate(np.array_split(unique_dates, n_splits)):
        df.loc[dates.isin(group), 'groups'] = i+1 
        
    groups = df.groups.values
    
    return groups


class TunedEstimator(BaseEstimator, ClassifierMixin,
                             MetaEstimatorMixin):
    """
    This class takes X,y as inputs and returns 
    a ML pipeline with optimized hyperparameters (through k-folds cross-validation)  
    that is also calibrated using isotonic regression. 
    
    
    Parameters
    -------------------
    estimator : unfit callable classifier or regressor (likely from scikit-learn) 
                    that implements a ``fit`` method. 
    
    pipeline_kwargs: dict (default is None). 
        The input args to the ml_workflow.preprocess.PreProcessingPipeline. 
        By default, no pipeline is used. 
        
    hyperopt_kwargs: dict (default is None).
        The input args to the ml_workflow.hyperparameter_optimizier.HyperOptCV
        excluding the estimator as that is handled internally in this class.
        By default, no hyperparameter optimization is performed. 
        
    calibration_cv_kwargs: dict (default is None).
        The input args to the sklearn.calibration.CalibratedClassifierCV excluding
        the baseestimator as that is handled internally in this class. 
        There is also internal code for the cross-validation if the user does not 
        specify the cross-validation. 
        By default, no calibration is performed. 
    
    
    Attributes
    ---------------
    model_ : The fit estimator (could be the original estimator, 
                a pipeline object, or a calibratedclassifier object)
    
    X_ : The training dataframe inputs 
    y_ : The target values 
    """
    def __init__(self, estimator, pipeline_kwargs=None, hyperopt_kwargs=None, calibration_cv_kwargs=None): 
        
        # Pipeline inputs
        self.pipeline_kwargs = {} if pipeline_kwargs is None else pipeline_kwargs

        # HyperOptCV inputs 
        self.hyperopt_kwargs = {} if hyperopt_kwargs is None else hyperopt_kwargs

        # CalibrationClassifier inputs 
        self.calibration_cv_kwargs = {} if calibration_cv_kwargs is None else calibration_cv_kwargs
        
        self.estimator = estimator 
        
    def get_cv(self, X, y, groups=None):
        
        # Initialize the cross-validation. 
        if groups is not None:
            group_kfold = GroupKFold(n_splits=5) 
            cv = group_kfold.split(X, y, groups)
        else:
            kfold = KFold(n_splits=5) 
            cv = kfold.split(X, y)
        
        return cv 
    
    def get_pipeline(self, estimator):
        if self.pipeline_kwargs:
            return PreProcessPipeline(**self.pipeline_kwargs).get_pipeline(estimator)
        else:
            return estimator
        
    def _fit_hyperopt(self, X, y, groups=None):
        
        cv = self.get_cv(X, y, groups)
        pipeline = self.get_pipeline(self.estimator)

        # Rather than introducing two imputers, we can simply replace 
        # inf vals with nans. 
        X.replace([np.inf, -np.inf], np.nan, inplace=True)
        
        self.hyperopt_kwargs['estimator'] = pipeline 
        
        hopt = HyperOptCV(**self.hyperopt_kwargs) 
        
        hopt.fit(X,y)
    
        return hopt.best_params_
    
    
    def fit(self, X, y, groups=None):
        """
        Fit the estimator 
        
        Parameters:
        ----------------
            X, pd.DataFrame, shape : (n_samples, n_features)
            y, shape: (n_samples, )
        
        """
        if not isinstance(X, pd.DataFrame):
            X = pd.DataFrame(X)
        
        cv = self.get_cv(X, y, groups)
        if self.hyperopt_kwargs:
            best_params = self._fit_hyperopt(X,y,groups)
            self.best_params_ = best_params
            pipeline = self.get_pipeline(self.estimator.set_params(**best_params))
        else:
            self.best_params_ = 'None'
            pipeline = self.get_pipeline(self.estimator)
        
        # Rather than introducing two imputers, we can simply replace 
        # inf vals with nans. 
        X.replace([np.inf, -np.inf], np.nan, inplace=True)

        self.X_ = X 
        self.y_ = y 
        
        
        # After hyperopt tuning, perform the calibration. 
        if self.calibration_cv_kwargs:
            if 'cv' not in self.calibration_cv_kwargs.keys():
                self.calibration_cv_kwargs['cv'] = cv
            
            self.calibration_cv_kwargs['base_estimator'] = pipeline
            
            self.model_ = CalibratedClassifierCV(**self.calibration_cv_kwargs)
        else:
            self.model_ = pipeline
            
    
        self.model_.fit(X,y)
        
        # To save the model. 
        if self.calibration_cv_kwargs:
            self.model_.cv = 'None'
    
    def predict_proba(self, X):
        """Posterior probabilities of classification

        This function returns posterior probabilities of classification
        according to each class on an array of test vectors X.

        Parameters
        ----------
        X : array-like, shape (n_samples, n_features)
            The samples.

        Returns
        -------
        C : array, shape (n_samples, n_classes)
            The predicted probas.
        """
        return self.model_.predict_proba(X) 
    
    def predict(self, X):
        """Model Predictions 

        Parameters
        ----------
        X : array-like, shape (n_samples, n_features)
            The samples.

        Returns
        -------
        C : array, shape (n_samples, n_classes)
            The predicted values.
        """
        return self.model_.predict(X) 
        
    def save(self, fname): 
        data = {'model' : self.model_, 
                'X' : self.X_, 
                'y' : self.y_,
               }
        joblib.dump(data, fname, compress=3)
        
        