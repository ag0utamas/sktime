#!/usr/bin/env python3 -u
# -*- coding: utf-8 -*-
# copyright: sktime developers, BSD-3-Clause License (see LICENSE file)

import numpy as np
import pandas as pd
from joblib import Parallel
from joblib import delayed
from sklearn.base import clone

from sktime.base import MetaEstimatorMixin
from sktime.transformers.series_as_features.base import BaseSeriesAsFeaturesTransformer
from sktime.transformers.series_as_features.base import (
    _NonFittableSeriesAsFeaturesTransformer,
)
from sktime.transformers.series_as_features.segment import RandomIntervalSegmenter
from sktime.utils.validation.series_as_features import check_X


class PlateauFinder(_NonFittableSeriesAsFeaturesTransformer):
    """Transformer that finds segments of the same given value, plateau in
    the time series, and
    returns the starting indices and lengths.

    Parameters
    ----------
    value : {int, float, np.nan, np.inf}
        Value for which to find segments
    min_length : int
        Minimum lengths of segments with same value to include.
        If min_length is set to 1, the transformer can be used as a value
        finder.
    """

    def __init__(self, value=np.nan, min_length=2):
        self.value = value
        self.min_length = min_length
        super(PlateauFinder, self).__init__()

    def transform(self, X, y=None):
        """Transform X.
        Parameters
        ----------
        X : nested pandas DataFrame of shape [n_samples, n_columns]
            Nested dataframe with time-series in cells.
        Returns
        -------
        Xt : pandas DataFrame
          Transformed pandas DataFrame
        """

        # input checks
        self.check_is_fitted()
        X = check_X(X, enforce_univariate=True, coerce_to_pandas=True)

        # get column name
        column_name = X.columns[0]

        self._starts = []
        self._lengths = []

        # find plateaus (segments of the same value)
        for x in X.iloc[:, 0]:
            x = np.asarray(x)

            # find indices of transition
            if np.isnan(self.value):
                i = np.where(np.isnan(x), 1, 0)

            elif np.isinf(self.value):
                i = np.where(np.isinf(x), 1, 0)

            else:
                i = np.where(x == self.value, 1, 0)

            # pad and find where segments transition
            transitions = np.diff(np.hstack([0, i, 0]))

            # compute starts, ends and lengths of the segments
            starts = np.where(transitions == 1)[0]
            ends = np.where(transitions == -1)[0]
            lengths = ends - starts

            # filter out single points
            starts = starts[lengths >= self.min_length]
            lengths = lengths[lengths >= self.min_length]

            self._starts.append(starts)
            self._lengths.append(lengths)

        # put into dataframe
        Xt = pd.DataFrame()
        column_prefix = "%s_%s" % (
            column_name,
            "nan" if np.isnan(self.value) else str(self.value),
        )
        Xt["%s_starts" % column_prefix] = pd.Series(self._starts)
        Xt["%s_lengths" % column_prefix] = pd.Series(self._lengths)
        return Xt


class DerivativeSlopeTransformer(BaseSeriesAsFeaturesTransformer):
    # TODO add docstrings
    def transform(self, X, y=None):
        self.check_is_fitted()
        X = check_X(X, enforce_univariate=False, coerce_to_pandas=True)

        num_cases, num_dim = X.shape
        output_df = pd.DataFrame()
        for dim in range(num_dim):
            dim_data = X.iloc[:, dim]
            out = self.row_wise_get_der(dim_data)
            output_df["der_dim_" + str(dim)] = pd.Series(out)

        return output_df

    @staticmethod
    def row_wise_get_der(X):
        def get_der(x):
            der = []
            for i in range(1, len(x) - 1):
                der.append(((x[i] - x[i - 1]) + ((x[i + 1] - x[i - 1]) / 2)) / 2)
            return pd.Series([der[0]] + der + [der[-1]])

        return [get_der(x) for x in X]


class RandomIntervalFeatureExtractor(RandomIntervalSegmenter):
    """
    Transformer that segments time-series into random intervals
    and subsequently extracts series-to-primitives features from each interval.

    n_intervals: str{'sqrt', 'log', 'random'}, int or float, optional (
    default='sqrt')
        Number of random intervals to generate, where m is length of time
        series:
        - If "log", log of m is used.
        - If "sqrt", sqrt of m is used.
        - If "random", random number of intervals is generated.
        - If int, n_intervals intervals are generated.
        - If float, int(n_intervals * m) is used with n_intervals giving the
        fraction of intervals of the
        time series length.

        For all arguments relative to the length of the time series,
        the generated number of intervals is
        always at least 1.

    features: list of functions, optional (default=None)
        Applies each function to random intervals to extract features.
        If None, the mean is extracted.

    random_state: : int, RandomState instance, optional (default=None)
        - If int, random_state is the seed used by the random number generator;
        - If RandomState instance, random_state is the random number generator;
        - If None, the random number generator is the RandomState instance used
        by `np.random`.
    """

    def __init__(
        self, n_intervals="sqrt", min_length=2, features=None, random_state=None
    ):
        super(RandomIntervalFeatureExtractor, self).__init__(
            n_intervals=n_intervals,
            min_length=min_length,
            random_state=random_state,
        )
        self.features = features

    def transform(self, X, y=None):
        """
        Transform X, segments time-series in each column into random
        intervals using interval indices generated
        during `fit` and extracts features from each interval.

        Parameters
        ----------
        X : nested pandas.DataFrame of shape [n_samples, n_features]
            Nested dataframe with time-series in cells.

        Returns
        -------
        Xt : pandas.DataFrame
          Transformed pandas DataFrame with same number of rows and one
          column for each generated interval.
        """
        # Check is fit had been called
        self.check_is_fitted()

        # Check input of feature calculators, i.e list of functions to be
        # applied to time-series
        if self.features is None:
            features = [np.mean]
        elif isinstance(self.features, list) and all(
            [callable(func) for func in self.features]
        ):
            features = self.features
        else:
            raise ValueError(
                "Features must be list containing only functions (callables) "
                "to be applied to the data columns"
            )

        X = check_X(X, enforce_univariate=True, coerce_to_numpy=True)

        # Check that the input is of the same shape as the one passed
        # during fit.
        if X.shape[1] != self.input_shape_[1]:
            raise ValueError(
                "Number of columns of input is different from what was seen" "in `fit`"
            )
        # Input validation
        # if not all([np.array_equal(fit_idx, trans_idx) for trans_idx,
        # fit_idx in zip(check_equal_index(X),
        #     raise ValueError('Indexes of input time-series are different
        #     from what was seen in `fit`')

        n_instances, n_columns, _ = X.shape
        n_features = len(features)

        n_intervals = len(self.intervals_)

        # Compute features on intervals.
        Xt = np.zeros((n_instances, n_features * n_intervals))  # Allocate output array
        # for transformed data
        columns = []
        colname = [f"col{i}" for i in range(n_columns)]

        i = 0
        for func in features:
            # TODO generalise to series-to-series functions and function kwargs
            for start, end in self.intervals_:
                interval = X[:, :, start:end]

                # Try to use optimised computations over axis if possible,
                # otherwise iterate over rows.
                try:
                    Xt[:, i] = func(interval, axis=-1).squeeze()
                except TypeError as e:
                    if (
                        str(e) == f"{func.__name__}() got an unexpected "
                        f"keyword argument 'axis'"
                    ):
                        Xt[:, i] = np.apply_along_axis(
                            func, axis=2, arr=interval
                        ).squeeze()
                    else:
                        raise
                i += 1
                columns.append(f"{colname}_{start}_{end}_{func.__name__}")

        Xt = pd.DataFrame(Xt)
        Xt.columns = columns
        return Xt


class FittedParamExtractor(MetaEstimatorMixin, _NonFittableSeriesAsFeaturesTransformer):
    """
    Extract parameters of a fitted forecaster as features for a subsequent
    tabular learning task.
    This class first fits a forecaster to the given time series and then
    returns the fitted parameters.
    The fitted parameters can be used as features for a tabular estimator
    (e.g. classification).

    Parameters
    ----------
    forecaster : estimator object
        sktime estimator to extract features from
    param_names : str
        Name of parameters to extract from the forecaster.
    n_jobs : int, optional (default=None)
        Number of jobs to run in parallel.
        None means 1 unless in a joblib.parallel_backend context.
        -1 means using all processors.
    """

    _required_parameters = ["forecaster"]

    def __init__(self, forecaster, param_names, n_jobs=None):
        self.forecaster = forecaster
        self.param_names = param_names
        self.n_jobs = n_jobs
        super(FittedParamExtractor, self).__init__()

    def transform(self, X, y=None):
        """

        Parameters
        ----------
        X : pd.DataFrame
            Univariate time series to transform.
        y : pd.DataFrame, optional (default=False)
            Exogenous variables

        Returns
        -------
        Xt : pd.DataFrame
            Extracted parameters; columns are parameter values
        """
        self.check_is_fitted()
        X = check_X(X, enforce_univariate=True)
        param_names = self._check_param_names(self.param_names)
        n_instances = X.shape[0]

        def _fit_extract(forecaster, x, param_names):
            forecaster.fit(x)
            params = forecaster.get_fitted_params()
            return np.hstack([params[name] for name in param_names])

        def _get_instance(X, key):
            # assuming univariate data
            if isinstance(X, pd.DataFrame):
                return X.iloc[key, 0]
            else:
                return pd.Series(X[key, 0])

        # iterate over rows
        extracted_params = Parallel(n_jobs=self.n_jobs)(
            delayed(_fit_extract)(
                clone(self.forecaster), _get_instance(X, i), param_names
            )
            for i in range(n_instances)
        )

        return pd.DataFrame(extracted_params, columns=param_names)

    @staticmethod
    def _check_param_names(param_names):
        if isinstance(param_names, str):
            param_names = [param_names]
        elif isinstance(param_names, (list, tuple)):
            for param in param_names:
                if not isinstance(param, str):
                    raise ValueError(
                        f"All elements of `param_names` must be strings, "
                        f"but found: {type(param)}"
                    )
        else:
            raise ValueError(
                f"`param_names` must be str, or a list or tuple of strings, "
                f"but found: {type(param_names)}"
            )
        return param_names