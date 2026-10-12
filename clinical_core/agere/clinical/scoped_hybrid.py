"""Input adapter for unchanged v2 heads within the relation-scoped hybrid."""
from sklearn.base import BaseEstimator, TransformerMixin
from .evidence_reader import input_feature


class WholeWindowAdapter(BaseEstimator, TransformerMixin):
    def fit(self, x, y=None):return self
    def transform(self, x):return [input_feature(row) for row in x]
