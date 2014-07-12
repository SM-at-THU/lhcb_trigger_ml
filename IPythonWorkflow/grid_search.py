# About

# this module is implementation of optimized grid_search,
# which uses some metropolis-like algorithm.

from __future__ import division
from __future__ import print_function
from itertools import islice
from warnings import warn
import numpy
import pandas
import sklearn
import logging

from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.cross_validation import StratifiedKFold
from sklearn.grid_search import _check_param_grid
from sklearn.metrics.metrics import roc_auc_score
from sklearn.utils.random import check_random_state
import commonutils
try:
    from collections import OrderedDict
except ImportError:
    from ordereddict import OrderedDict

__author__ = 'Alex Rogozhnikov'

# TODO think of simulated annealing, regression, sub grids and other techniques.
# TODO pareto-optimization
# TODO optimization of mse or whatever dependent on X
# TODO use staged predictions


class AbstractParameterGenerator(object):
    def __init__(self, param_grid, n_evaluations, random_state=None):
        """
        The aim of this class is to generate new points, where the function (estimator) will be computed.
        :type param_grid: OrderedDict, the grid with parameters to optimize on
        :type n_evaluations: int, the number of evaluations to do
        :type random_state: int | RandomState | None
        """
        assert isinstance(param_grid, dict), 'the passed param_grid should be of OrderedDict class'
        self.param_grid = OrderedDict(param_grid)
        _check_param_grid(self.param_grid)
        self.dimensions = list([len(param_values) for param, param_values in self.param_grid.iteritems()])
        size = numpy.prod(self.dimensions)
        assert size > 1, 'The space of parameters contains only %i points' % size
        if n_evaluations > size / 2:
            warn('The number of evaluations was decreased to %i' % (size // 2), UserWarning)
            n_evaluations = size // 2
        self.n_evaluations = n_evaluations
        # results on different parameters
        self.grid_scores_ = OrderedDict()
        # all the tasks that are being computed or already computed
        self.queued_tasks_ = set()
        self.random_state = check_random_state(random_state)
        self.evaluations_done = 0

    def indices_to_parameters(self, state_indices):
        return OrderedDict([(name, values[i]) for i, (name, values) in zip(state_indices, self.param_grid.iteritems())])

    def _generate_start_point(self):
        while True:
            result = tuple([self.random_state.randint(0, size) for size in self.dimensions])
            if result not in self.queued_tasks_:
                self.queued_tasks_.add(result)
                return result

    def generate_next_point(self):
        raise NotImplementedError("To be derived in descendants")

    def generate_batch_points(self, size):
        # may be overriden in descendants
        state_indices = []
        for _ in range(size):
            state_indices.append(self.generate_next_point())
        return zip(*state_indices)

    def add_result(self, key, value):
        """key is an n-tuple with integers"""
        self.grid_scores_[key] = value

    @property
    def best_score_(self):
        return numpy.max(self.grid_scores_.values())

    @property
    def best_params_(self):
        return self.indices_to_parameters(max(self.grid_scores_.iteritems(), key=lambda x: x[1])[0])

    def print_results(self, reorder=True):
        """Prints the results of training, if reorder==True, best results go earlier,
        otherwise the results are printed in the order of computation"""
        sequence = self.grid_scores_.iteritems()
        if reorder:
            sequence = sorted(sequence, key=lambda x: -x[1])
        for state_indices, value in sequence:
            state_string = ", ".join([name_value[0] + '=' + str(name_value[1]) for name_value
                                      in self.indices_to_parameters(state_indices).iteritems()])
            print("{0:.3f}:  {1}".format(value, state_string))

    @property
    def results_dataframe_(self):
        sequence = sorted(self.grid_scores_.iteritems(), key=lambda x: x[1])
        data = []
        for state_indices, value in sequence:
            data.append(self.indices_to_parameters(state_indices))
        return pandas.DataFrame(data).transpose()


def create_subgrid(param_grid, n_values):
    """
    :type param_grid: OrderedDict,
    :type n_values: int, the maximal number of values along each axis
    :rtype: (OrderedDict, OrderedDict), the subgrid and the indices of values that form subgrid
    """
    subgrid = OrderedDict()
    subgrid_indices = OrderedDict()
    for key, values in param_grid.iteritems():
        if len(values) <= n_values:
            subgrid[key] = list(values)
            subgrid_indices[key] = range(len(values))
        else:
            # numpy.rint rounds to the nearest integer
            axis_indices = numpy.rint(numpy.linspace(-0.5, len(values)-0.5, 2 * n_values + 1)[1::2]).astype(int)
            subgrid[key] = [values[index] for index in axis_indices]
            subgrid_indices[key] = axis_indices
    return subgrid, subgrid_indices


def translate_key_from_subgrid(subgrid_indices, key):
    """
    :type key: tuple, the indices (describing the point) in subgrid
    :type subgrid_indices: OrderedDict, the indices of values taken to form subgrid
    :rtype: tuple, the indices in grid
    """
    return tuple([subgrid_indices[var_name][index] for var_name, index in zip(subgrid_indices, key)])


class SimpleParameterOptimizer(AbstractParameterGenerator):
    def __init__(self, param_grid, n_evaluations, random_state=None, start_evaluations=3, portion=None,
                 subgrid_size=3):
        AbstractParameterGenerator.__init__(self, param_grid=param_grid, n_evaluations=n_evaluations,
                                            random_state=random_state)
        self.start_evaluations = start_evaluations
        self.portion = portion
        self.subgrid_size = subgrid_size
        self.subgrid_parameter_generator = None
        if not numpy.all(numpy.array(self.dimensions) <= 1.5 * self.subgrid_size):
            print("Optimizing on subgrid")
            param_subgrid, self.subgrid_indices = create_subgrid(self.param_grid, self.subgrid_size)
            self.subgrid_parameter_generator = \
                SimpleParameterOptimizer(param_subgrid, n_evaluations=self.n_evaluations//2,
                                         portion=portion, subgrid_size=subgrid_size)

    def generate_next_point(self):
        """Generating next point in parameters space"""
        if self.subgrid_parameter_generator is not None:
            # trying to generate from subgrid
            if len(self.queued_tasks_) < self.subgrid_parameter_generator.n_evaluations:
                indices, parameters = self.subgrid_parameter_generator.generate_next_point()
                self.queued_tasks_.add(translate_key_from_subgrid(self.subgrid_indices, indices))
                return ('subgrid', indices), parameters

        if len(self.grid_scores_) <= 4:
            indices = self._generate_start_point()
            self.queued_tasks_.add(indices)
            return indices, self.indices_to_parameters(indices)

        results = numpy.array(self.grid_scores_.values())
        std = numpy.std(results) + 1e-5
        probabilities = numpy.exp(numpy.clip((results - numpy.mean(results)) * 3. / std, -5, 5))
        probabilities /= numpy.sum(probabilities)
        while True:
            start = self.random_state.choice(len(probabilities), p=probabilities)
            start_indices = self.grid_scores_.keys()[start]
            axis = self.random_state.randint(len(self.dimensions))
            new_state_indices = list(start_indices)
            new_state_indices[axis] += 1 if self.random_state.uniform() > 0.5 else -1
            if new_state_indices[axis] < 0 or new_state_indices[axis] >= self.dimensions[axis]:
                continue
            new_state_indices = tuple(new_state_indices)
            if new_state_indices in self.queued_tasks_:
                continue
            self.queued_tasks_.add(new_state_indices)
            return new_state_indices, self.indices_to_parameters(new_state_indices)

    def add_result(self, state_indices, value):
        if state_indices[0] == 'subgrid':
            self.grid_scores_[translate_key_from_subgrid(self.subgrid_indices, state_indices[1])] = value
            self.subgrid_parameter_generator.add_result(state_indices[1], value)
        else:
            self.grid_scores_[state_indices] = value


class FunctionOptimizer(object):
    """Class was created to test different optimizing algorithms on functions,
    it gets any function of several variables and just optimizes it"""
    def __init__(self, function, param_grid, n_evaluations=100, parameter_generator_type=None):
        """
        :type function: some function, we are looking for its maximal value.
        :type parameter_generator_type: (param_grid, n_evaluations) -> AbstractParameterGenerator
        """
        self.function = function
        if parameter_generator_type is None:
            parameter_generator_type = SimpleParameterOptimizer
        self.generator = parameter_generator_type(param_grid, n_evaluations)

    def optimize(self):
        assert isinstance(self.generator, AbstractParameterGenerator), 'the generator should be an instance of ' \
            'abstract parameter generator'
        for _ in range(self.generator.n_evaluations):
            next_indices, next_params = self.generator.generate_next_point()
            value = self.function(**next_params)
            self.generator.add_result(state_indices=next_indices, value=value)

    def print_results(self, reorder=True):
        self.generator.print_results(reorder=reorder)


def test_simple_optimizer(n_evaluations=60):
    optimizer = FunctionOptimizer(lambda x, y, z, w: x * y * z * w,
                                  param_grid={'x': range(11), 'y': range(11), 'z': range(11), 'w': range(11)},
                                  n_evaluations=n_evaluations)
    optimizer.optimize()
    assert len(optimizer.generator.grid_scores_) == n_evaluations
    assert len(optimizer.generator.queued_tasks_) == n_evaluations
    assert set(optimizer.generator.grid_scores_.keys()) == optimizer.generator.queued_tasks_

test_simple_optimizer()


def estimate_classifier(params_dict, base_estimator, X, y, folds, fold_checks,
                        score_function, sample_weight=None, label=1, scorer_needs_X=False):
    """This function is needed """
    k_folder = StratifiedKFold(y=y, n_folds=folds)
    score = 0.
    for train_indices, test_indices in islice(k_folder, fold_checks):
        trainX, trainY = X.irow(train_indices), y[train_indices]
        testX, testY = X.irow(test_indices), y[test_indices]
        estimator = sklearn.clone(base_estimator).set_params(**params_dict)

        train_options = {}
        test_options = {}
        if sample_weight is not None:
            train_weights, test_weights = \
                sample_weight[train_indices], sample_weight[test_indices]
            train_options['sample_weight'] = train_weights
            test_options['sample_weight'] = test_weights
        if scorer_needs_X:
            test_options['X'] = testX

        estimator.fit(trainX, trainY, **train_options)
        proba = estimator.predict_proba(testX)
        score += score_function(testY, proba[:, label], **test_options)

    return score / fold_checks


class GridOptimalSearchCV(BaseEstimator, ClassifierMixin):
    def __init__(self, base_estimator, param_grid, n_evaluations=40, score_function=None, folds=3, fold_checks=1,
                 scorer_needs_x=False, ipc_profile=None, param_generator_type=None,
                 random_state=None):
        """Optimal search over specified parameter values for an estimator. Metropolis-like algorithm is used
        Important members are fit, predict.

        GridSearchCV implements a "fit" method and a "predict" method like any classifier except that
        the parameters of the classifier used to predict is optimized by cross-validation.

        Parameters
        ----------
        base_estimator : object of type that implements the "fit" and "predict" methods
            A new object of that type is cloned for each point.

        param_grid : dict
            Dictionary with parameters names (string) as keys and lists of
            parameter settings to try as values. The closest values in the list are considered
            to give the closest results

        score_function : callable or None, callable
            A string (see model evaluation documentation) or
            a scorer callable object / function with signature
            ``scorer(estimator, X, y)``.

        folds: int, 'k' used in k-folding while validating

        fold_checks: int, not greater than folds, the number of checks we do by cross-validating

        n_evaluations : int,
            The number of attempts of evaluations, will be truncated

        random_state: int or None or RandomState object,
            used to generate random numbers

        scorer_needs_x: bool, if True, then test X (dataframe) is passed
            to the scoring function.

        ipc_profile: str, the name of IPython parallel cluster profile to use,
            or None to perform computations locally

        Attributes
        ----------
        `grid_scores_` : list of named tuples
            Contains scores for all parameter combinations in param_grid.
            Each entry corresponds to one parameter setting.
            Each named tuple has the attributes:

                * ``parameters``, a dict of parameter settings
                * ``mean_validation_score``, the mean score over the
                  cross-validation folds
                * ``cv_validation_scores``, the list of scores for each fold

        `best_estimator_` : estimator
            Estimator that was chosen by the search, i.e. estimator
            which gave highest score (or smallest loss if specified)
            on the left out data.
        """
        self.base_estimator = base_estimator
        self.param_grid = param_grid
        self.n_evaluations = n_evaluations
        self.ipc_profile = ipc_profile
        self.score_function = score_function
        self.folds = folds
        self.fold_checks = fold_checks
        self.param_generator_type = param_generator_type
        self.random_state = random_state

    def _log(self, *objects):
        logging.debug(" ".join([str(x) for x in objects]))

    def _check_params(self):
        if self.param_generator_type is None:
            self.param_generator_type = SimpleParameterOptimizer
        self.generator = self.param_generator_type(self.param_grid, self.n_evaluations)
        # Deleting parameters
        self.n_evaluations = None
        self.param_grid = None

        if self.score_function is None:
            self.score_function = roc_auc_score
        assert self.fold_checks <= self.folds, "We cannot have more checks than folds"
        self.random_state = check_random_state(self.random_state)

    def fit(self, X, y, sample_weight=None):
        self._check_params()
        self.evaluations_done = 0
        X = pandas.DataFrame(X)

        if self.ipc_profile is None:
            while self.evaluations_done < self.generator.n_evaluations:
                state_indices, state_dict = self.generator.generate_next_point()
                value = estimate_classifier(params_dict=state_dict, base_estimator=self.base_estimator,
                                            X=X, y=y, folds=self.folds, fold_checks=self.fold_checks,
                                            score_function=self.score_function, sample_weight=sample_weight)
                self.generator.add_result(state_indices, value)
                self.evaluations_done += 1
                state_string = ", ".join([k + '=' + str(v) for k, v in state_dict.iteritems()])
                self._log(value, ": ", state_string)
        else:
            from IPython.parallel import Client
            direct_view = Client(profile=self.ipc_profile).direct_view()
            portion = len(direct_view)
            print("There are {0} cores in cluster, the portion is equal {1}".format(len(direct_view), portion))
            while self.evaluations_done < self.generator.n_evaluations:
                state_indices_array, state_dict_array = self.generator.generate_batch_points(size=portion)
                result = direct_view.map_sync(estimate_classifier, state_dict_array,
                    [self.base_estimator] * portion, [X]*portion, [y]*portion,
                    [self.folds] * portion, [self.fold_checks] * portion,
                    [self.score_function] * portion, [sample_weight]*portion)
                assert len(result) == portion, "The length of result is very strange"
                for state_indices, state_dict, score in zip(state_indices_array, state_dict_array, result):
                    self.generator.add_result(state_indices, score)
                    self._log(score, ": ", ", ".join([k + '=' + str(v) for k, v in state_dict]))

                self.evaluations_done += portion
                print("%i evaluations done" % self.evaluations_done)

        self._fit_best_estimator(X, y, sample_weight=sample_weight)

    def _fit_best_estimator(self, X, y, sample_weight=None):
        # Training classifier once again
        self.best_estimator_ = sklearn.clone(self.base_estimator).set_params(**self.generator.best_params_)
        if sample_weight is None:
            self.best_estimator_.fit(X, y)
        else:
            self.best_estimator_.fit(X, y, sample_weight=sample_weight)

    @property
    def grid_scores_(self):
        return self.generator.grid_scores_

    def predict_proba(self, X):
        return self.best_estimator_.predict_proba(X)

    def predict(self, X):
        return self.best_estimator_.predict(X)

    def print_results(self, reorder=True):
        self.generator.print_results(reorder=reorder)


class TestClassifier(BaseEstimator, ClassifierMixin):
    """This classifier is created specially for testing optimization"""
    def __init__(self, a=1., b=1., c=1., d=1., sign=1):
        self.a = a
        self.b = b
        self.c = c
        self.d = d
        self.sign = sign

    def fit(self, X, y, sample_weight=None):
        pass

    def predict_proba(self, X):
        return numpy.zeros([len(X), 2]) + self.a * self.b * self.c * self.d * self.sign


def mean_score(y, pred, sample_weight=None):
    """ This metrics was created for testing purposes"""
    return numpy.mean(pred)


def test_optimization(size=10, n_evaluations=150):
    trainX, trainY = commonutils.generate_sample(2000, 10, distance=0.5)

    grid_1d = numpy.linspace(0.1, 1, num=size)
    grid = {'a': grid_1d, 'b': grid_1d, 'c': grid_1d, 'd': grid_1d}
    grid = OrderedDict(grid)

    grid_cv = GridOptimalSearchCV(TestClassifier(), grid, n_evaluations=n_evaluations, score_function=mean_score)
    grid_cv.fit(trainX, trainY)
    optimizer = grid_cv.generator
    assert 0.8 <= optimizer.best_score_ <= 1., 'Too poor optimization : %.2f' % optimizer.best_score_
    assert mean_score(trainY, grid_cv.predict_proba(trainX)[:, 1]) == optimizer.best_score_, 'something is wrong'

    grid_cv = GridOptimalSearchCV(TestClassifier(sign=-1), grid, n_evaluations=n_evaluations, score_function=mean_score)
    grid_cv.fit(trainX, trainY)
    optimizer = grid_cv.generator
    assert -0.04 <= optimizer.best_score_ <= 0.0, 'Too poor optimization : %.2f' % optimizer.best_score_


test_optimization()


def test_grid_search():
    from sklearn.ensemble import AdaBoostClassifier
    from sklearn.tree import DecisionTreeClassifier, ExtraTreeClassifier
    grid = {'base_estimator': [DecisionTreeClassifier(max_depth=3), DecisionTreeClassifier(max_depth=4),
                               ExtraTreeClassifier(max_depth=4)],
            'learning_rate': [0.01, 0.1, 0.5, 1.],
            'n_estimators': [5, 10, 15, 20, 30, 40, 50, 75, 100, 125],
            'algorithm': ['SAMME', 'SAMME.R']}
    grid = OrderedDict(grid)

    trainX, trainY = commonutils.generate_sample(2000, 10, distance=0.5)
    grid_cv = GridOptimalSearchCV(AdaBoostClassifier(), grid, n_evaluations=10)
    grid_cv.fit(trainX, trainY)
    grid_cv.predict_proba(trainX)
    grid_cv.predict(trainX)

test_grid_search()