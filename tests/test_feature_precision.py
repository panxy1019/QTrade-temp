import numpy as np
import pytest

from qtrade.research.feature_precision import float64_features


def test_feature_precision_preserves_values_names_order_labels_and_original_frame():
    pd = pytest.importorskip('pandas')
    columns = pd.MultiIndex.from_tuples([('feature', f'F{i}') for i in range(158)]
                                        + [('label', 'Y')])
    original = pd.DataFrame(np.arange(318, dtype='float32').reshape(2, 159), columns=columns)
    original.iloc[0, 0] = np.nan
    saved = original.copy(deep=True)
    result = float64_features(original)
    pd.testing.assert_frame_equal(original, saved)
    assert result.columns.equals(original.columns) and result.index.equals(original.index)
    assert set(result['feature'].dtypes.astype(str)) == {'float64'}
    assert str(result['label']['Y'].dtype) == 'float32'
    np.testing.assert_array_equal(result.to_numpy(), original.to_numpy())


def test_unknown_or_duplicate_feature_contract_is_rejected():
    pd = pytest.importorskip('pandas')
    with pytest.raises(ValueError, match='CONTRACT'):
        float64_features(pd.DataFrame({'not_feature': [1]}))
