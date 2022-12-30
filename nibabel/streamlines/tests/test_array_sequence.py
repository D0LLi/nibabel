import itertools
import os
import sys
import tempfile
import unittest

import numpy as np
import pytest
from numpy.testing import assert_array_equal

from ...testing import assert_arrays_equal
from ..array_sequence import ArraySequence, concatenate, is_array_sequence

SEQ_DATA = {}


def setup_module():
    global SEQ_DATA
    rng = np.random.RandomState(42)
    SEQ_DATA['rng'] = rng
    SEQ_DATA['data'] = generate_data(nb_arrays=5, common_shape=(3,), rng=rng)
    SEQ_DATA['seq'] = ArraySequence(SEQ_DATA['data'])


def generate_data(nb_arrays, common_shape, rng):
    data = [rng.rand(*(rng.randint(3, 20),) + common_shape) * 100 for _ in range(nb_arrays)]
    return data


def check_empty_arr_seq(seq):
    assert len(seq) == 0
    assert len(seq._offsets) == 0
    assert len(seq._lengths) == 0
    # assert_equal(seq._data.ndim, 0)
    assert seq._data.ndim == 1
    assert seq.common_shape == ()


def check_arr_seq(seq, arrays):
    lengths = list(map(len, arrays))
    assert is_array_sequence(seq)
    assert len(seq) == len(arrays)
    assert len(seq._offsets) == len(arrays)
    assert len(seq._lengths) == len(arrays)
    assert seq._data.shape[1:] == arrays[0].shape[1:]
    assert seq.common_shape == arrays[0].shape[1:]

    assert_arrays_equal(seq, arrays)

    # If seq is a view, then order of internal data is not guaranteed.
    if seq._is_view:
        # The only thing we can check is the _lengths.
        assert_array_equal(sorted(seq._lengths), sorted(lengths))
    else:
        seq.shrink_data()

        assert seq._data.shape[0] == sum(lengths)

        assert_array_equal(seq._data, np.concatenate(arrays, axis=0))
        assert_array_equal(seq._offsets, np.r_[0, np.cumsum(lengths)[:-1]])
        assert_array_equal(seq._lengths, lengths)


def check_arr_seq_view(seq_view, seq):
    assert seq_view._is_view
    assert seq_view is not seq
    assert np.may_share_memory(seq_view._data, seq._data)
    assert seq_view._offsets is not seq._offsets
    assert seq_view._lengths is not seq._lengths


class TestArraySequence(unittest.TestCase):
    def test_creating_empty_arraysequence(self):
        check_empty_arr_seq(ArraySequence())

    def test_creating_arraysequence_from_list(self):
        # Empty list
        check_empty_arr_seq(ArraySequence([]))

        # List of ndarrays.
        N = 5
        for ndim in range(1, N + 1):
            common_shape = tuple([SEQ_DATA['rng'].randint(1, 10) for _ in range(ndim - 1)])
            data = generate_data(nb_arrays=5, common_shape=common_shape, rng=SEQ_DATA['rng'])
            check_arr_seq(ArraySequence(data), data)

        # Force ArraySequence constructor to use buffering.
        buffer_size = 1.0 / 1024**2  # 1 bytes
        check_arr_seq(ArraySequence(iter(SEQ_DATA['data']), buffer_size), SEQ_DATA['data'])

    def test_creating_arraysequence_from_generator(self):
        gen_1, gen_2 = itertools.tee((e for e in SEQ_DATA['data']))
        seq = ArraySequence(gen_1)
        seq_with_buffer = ArraySequence(gen_2, buffer_size=256)

        # Check buffer size effect
        assert seq_with_buffer.get_data().shape == seq.get_data().shape
        assert seq_with_buffer._buffer_size > seq._buffer_size

        # Check generator result
        check_arr_seq(seq, SEQ_DATA['data'])
        check_arr_seq(seq_with_buffer, SEQ_DATA['data'])

        # Already consumed generator
        check_empty_arr_seq(ArraySequence(gen_1))

    def test_creating_arraysequence_from_arraysequence(self):
        seq = ArraySequence(SEQ_DATA['data'])
        check_arr_seq(ArraySequence(seq), SEQ_DATA['data'])

        # From an empty ArraySequence
        seq = ArraySequence()
        check_empty_arr_seq(ArraySequence(seq))

    def test_arraysequence_iter(self):
        assert_arrays_equal(SEQ_DATA['seq'], SEQ_DATA['data'])

        # Try iterating through a corrupted ArraySequence object.
        seq = SEQ_DATA['seq'].copy()
        seq._lengths = seq._lengths[::2]
        with pytest.raises(ValueError):
            list(seq)

    def test_arraysequence_copy(self):
        orig = SEQ_DATA['seq']
        seq = orig.copy()
        n_rows = seq.total_nb_rows
        assert n_rows == orig.total_nb_rows
        assert_array_equal(seq._data, orig._data[:n_rows])
        assert seq._data is not orig._data
        assert_array_equal(seq._offsets, orig._offsets)
        assert seq._offsets is not orig._offsets
        assert_array_equal(seq._lengths, orig._lengths)
        assert seq._lengths is not orig._lengths
        assert seq.common_shape == orig.common_shape

        # Taking a copy of an `ArraySequence` generated by slicing.
        # Only keep needed data.
        seq = orig[::2].copy()
        check_arr_seq(seq, SEQ_DATA['data'][::2])
        assert seq._data is not orig._data

    def test_arraysequence_append(self):
        element = generate_data(
            nb_arrays=1, common_shape=SEQ_DATA['seq'].common_shape, rng=SEQ_DATA['rng']
        )[0]

        # Append a new element.
        seq = SEQ_DATA['seq'].copy()  # Copy because of in-place modification.
        seq.append(element)
        check_arr_seq(seq, SEQ_DATA['data'] + [element])

        # Append a list of list.
        seq = SEQ_DATA['seq'].copy()  # Copy because of in-place modification.
        seq.append(element.tolist())
        check_arr_seq(seq, SEQ_DATA['data'] + [element])

        # Append to an empty ArraySequence.
        seq = ArraySequence()
        seq.append(element)
        check_arr_seq(seq, [element])

        # Append an empty array.
        seq = SEQ_DATA['seq'].copy()  # Copy because of in-place modification.
        seq.append([])
        check_arr_seq(seq, SEQ_DATA['seq'])

        # Append an element with different shape.
        element = generate_data(
            nb_arrays=1, common_shape=SEQ_DATA['seq'].common_shape * 2, rng=SEQ_DATA['rng']
        )[0]
        with pytest.raises(ValueError):
            seq.append(element)

    def test_arraysequence_extend(self):
        new_data = generate_data(
            nb_arrays=10, common_shape=SEQ_DATA['seq'].common_shape, rng=SEQ_DATA['rng']
        )

        # Extend with an empty list.
        seq = SEQ_DATA['seq'].copy()  # Copy because of in-place modification.
        seq.extend([])
        check_arr_seq(seq, SEQ_DATA['data'])

        # Extend with a list of ndarrays.
        seq = SEQ_DATA['seq'].copy()  # Copy because of in-place modification.
        seq.extend(new_data)
        check_arr_seq(seq, SEQ_DATA['data'] + new_data)

        # Extend with a generator.
        seq = SEQ_DATA['seq'].copy()  # Copy because of in-place modification.
        seq.extend((d for d in new_data))
        check_arr_seq(seq, SEQ_DATA['data'] + new_data)

        # Extend with another `ArraySequence` object.
        seq = SEQ_DATA['seq'].copy()  # Copy because of in-place modification.
        seq.extend(ArraySequence(new_data))
        check_arr_seq(seq, SEQ_DATA['data'] + new_data)

        # Extend with an `ArraySequence` view (e.g. been sliced).
        # Need to make sure we extend only the data we need.
        seq = SEQ_DATA['seq'].copy()  # Copy because of in-place modification.
        seq.extend(ArraySequence(new_data)[::2])
        check_arr_seq(seq, SEQ_DATA['data'] + new_data[::2])

        # Test extending an empty ArraySequence
        seq = ArraySequence()
        seq.extend(ArraySequence())
        check_empty_arr_seq(seq)

        seq.extend(SEQ_DATA['seq'])
        check_arr_seq(seq, SEQ_DATA['data'])

        # Extend with elements of different shape.
        data = generate_data(
            nb_arrays=10, common_shape=SEQ_DATA['seq'].common_shape * 2, rng=SEQ_DATA['rng']
        )
        seq = SEQ_DATA['seq'].copy()  # Copy because of in-place modification.
        with pytest.raises(ValueError):
            seq.extend(data)

        # Extend after extracting some slice
        working_slice = seq[:2]
        seq.extend(ArraySequence(new_data))

    def test_arraysequence_getitem(self):
        # Get one item
        for i, e in enumerate(SEQ_DATA['seq']):
            assert_array_equal(SEQ_DATA['seq'][i], e)

        # Get all items using indexing (creates a view).
        indices = list(range(len(SEQ_DATA['seq'])))
        seq_view = SEQ_DATA['seq'][indices]
        check_arr_seq_view(seq_view, SEQ_DATA['seq'])
        # We took all elements so the view should match the original.
        check_arr_seq(seq_view, SEQ_DATA['seq'])

        # Get multiple items using ndarray of dtype integer.
        for dtype in [np.int8, np.int16, np.int32, np.int64]:
            seq_view = SEQ_DATA['seq'][np.array(indices, dtype=dtype)]
            check_arr_seq_view(seq_view, SEQ_DATA['seq'])
            # We took all elements so the view should match the original.
            check_arr_seq(seq_view, SEQ_DATA['seq'])

        # Get multiple items out of order (creates a view).
        SEQ_DATA['rng'].shuffle(indices)
        seq_view = SEQ_DATA['seq'][indices]
        check_arr_seq_view(seq_view, SEQ_DATA['seq'])
        check_arr_seq(seq_view, [SEQ_DATA['data'][i] for i in indices])

        # Get slice (this will create a view).
        seq_view = SEQ_DATA['seq'][::2]
        check_arr_seq_view(seq_view, SEQ_DATA['seq'])
        check_arr_seq(seq_view, SEQ_DATA['data'][::2])

        # Use advanced indexing with ndarray of data type bool.
        selection = np.array([False, True, True, False, True])
        seq_view = SEQ_DATA['seq'][selection]
        check_arr_seq_view(seq_view, SEQ_DATA['seq'])
        check_arr_seq(seq_view, [SEQ_DATA['data'][i] for i, keep in enumerate(selection) if keep])

        # Test invalid indexing
        with pytest.raises(TypeError):
            SEQ_DATA['seq']['abc']

        # Get specific columns.
        seq_view = SEQ_DATA['seq'][:, 2]
        check_arr_seq_view(seq_view, SEQ_DATA['seq'])
        check_arr_seq(seq_view, [d[:, 2] for d in SEQ_DATA['data']])

        # Combining multiple slicing and indexing operations.
        seq_view = SEQ_DATA['seq'][::-2][:, 2]
        check_arr_seq_view(seq_view, SEQ_DATA['seq'])
        check_arr_seq(seq_view, [d[:, 2] for d in SEQ_DATA['data'][::-2]])

    def test_arraysequence_setitem(self):
        # Set one item
        seq = SEQ_DATA['seq'] * 0
        for i, e in enumerate(SEQ_DATA['seq']):
            seq[i] = e

        check_arr_seq(seq, SEQ_DATA['seq'])

        # Setitem with a scalar.
        seq = SEQ_DATA['seq'].copy()
        seq[:] = 0
        assert seq._data.sum() == 0

        # Setitem with a list of ndarray.
        seq = SEQ_DATA['seq'] * 0
        seq[:] = SEQ_DATA['data']
        check_arr_seq(seq, SEQ_DATA['data'])

        # Setitem using tuple indexing.
        seq = ArraySequence(np.arange(900).reshape((50, 6, 3)))
        seq[:, 0] = 0
        assert seq._data[:, 0].sum() == 0

        # Setitem using tuple indexing.
        seq = ArraySequence(np.arange(900).reshape((50, 6, 3)))
        seq[range(len(seq))] = 0
        assert seq._data.sum() == 0

        # Setitem of a slice using another slice.
        seq = ArraySequence(np.arange(900).reshape((50, 6, 3)))
        seq[0:4] = seq[5:9]
        check_arr_seq(seq[0:4], seq[5:9])

        # Setitem between array sequences with different number of sequences.
        seq = ArraySequence(np.arange(900).reshape((50, 6, 3)))
        with pytest.raises(ValueError):
            seq[0:4] = seq[5:10]

        # Setitem between array sequences with different amount of points.
        seq1 = ArraySequence(np.arange(10).reshape(5, 2))
        seq2 = ArraySequence(np.arange(15).reshape(5, 3))
        with pytest.raises(ValueError):
            seq1[0:5] = seq2

        # Setitem between array sequences with different common shape.
        seq1 = ArraySequence(np.arange(12).reshape(2, 2, 3))
        seq2 = ArraySequence(np.arange(8).reshape(2, 2, 2))

        with pytest.raises(ValueError):
            seq1[0:2] = seq2

        # Invalid index.
        with pytest.raises(TypeError):
            seq[object()] = None

    def test_arraysequence_operators(self):
        # Disable division per zero warnings.
        flags = np.seterr(divide='ignore', invalid='ignore')
        SCALARS = [42, 0.5, True, -3, 0]
        CMP_OPS = ['__eq__', '__ne__', '__lt__', '__le__', '__gt__', '__ge__']

        seq = SEQ_DATA['seq'].copy()
        seq_int = SEQ_DATA['seq'].copy()
        seq_int._data = seq_int._data.astype(int)
        seq_bool = SEQ_DATA['seq'].copy() > 30

        ARRSEQS = [seq, seq_int, seq_bool]
        VIEWS = [seq[::2], seq_int[::2], seq_bool[::2]]

        def _test_unary(op, arrseq):
            orig = arrseq.copy()
            seq = getattr(orig, op)()
            assert seq is not orig
            check_arr_seq(seq, [getattr(d, op)() for d in orig])

        def _test_binary(op, arrseq, scalars, seqs, inplace=False):
            for scalar in scalars:
                orig = arrseq.copy()
                seq = getattr(orig, op)(scalar)
                assert (seq is orig) == inplace

                check_arr_seq(seq, [getattr(e, op)(scalar) for e in arrseq])

            # Test math operators with another ArraySequence.
            for other in seqs:
                orig = arrseq.copy()
                seq = getattr(orig, op)(other)
                assert seq is not SEQ_DATA['seq']
                check_arr_seq(seq, [getattr(e1, op)(e2) for e1, e2 in zip(arrseq, other)])

            # Operations between array sequences of different lengths.
            orig = arrseq.copy()
            with pytest.raises(ValueError):
                getattr(orig, op)(orig[::2])

            # Operations between array sequences with different amount of data.
            seq1 = ArraySequence(np.arange(10).reshape(5, 2))
            seq2 = ArraySequence(np.arange(15).reshape(5, 3))
            with pytest.raises(ValueError):
                getattr(seq1, op)(seq2)

            # Operations between array sequences with different common shape.
            seq1 = ArraySequence(np.arange(12).reshape(2, 2, 3))
            seq2 = ArraySequence(np.arange(8).reshape(2, 2, 2))
            with pytest.raises(ValueError):
                getattr(seq1, op)(seq2)

        for op in [
            '__add__',
            '__sub__',
            '__mul__',
            '__mod__',
            '__floordiv__',
            '__truediv__',
        ] + CMP_OPS:
            _test_binary(op, seq, SCALARS, ARRSEQS)
            _test_binary(op, seq_int, SCALARS, ARRSEQS)

            # Test math operators with ArraySequence views.
            _test_binary(op, seq[::2], SCALARS, VIEWS)
            _test_binary(op, seq_int[::2], SCALARS, VIEWS)

            if op in CMP_OPS:
                continue

            op = f"__i{op.strip('_')}__"
            _test_binary(op, seq, SCALARS, ARRSEQS, inplace=True)

            if op == '__itruediv__':
                continue  # Going to deal with it separately.

            _test_binary(
                op, seq_int, [42, -3, True, 0], [seq_int, seq_bool, -seq_int], inplace=True
            )  # int <-- int

            with pytest.raises(TypeError):
                _test_binary(op, seq_int, [0.5], [], inplace=True)  # int <-- float
            with pytest.raises(TypeError):
                _test_binary(op, seq_int, [], [seq], inplace=True)  # int <-- float

        # __pow__ : Integers to negative integer powers are not allowed.
        _test_binary('__pow__', seq, [42, -3, True, 0], [seq_int, seq_bool, -seq_int])
        _test_binary(
            '__ipow__', seq, [42, -3, True, 0], [seq_int, seq_bool, -seq_int], inplace=True
        )

        with pytest.raises(ValueError):
            _test_binary('__pow__', seq_int, [-3], [])
        with pytest.raises(ValueError):
            _test_binary('__ipow__', seq_int, [-3], [], inplace=True)

        # __itruediv__ is only valid with float arrseq.
        for scalar in SCALARS + ARRSEQS:
            seq_int_cp = seq_int.copy()
            with pytest.raises(TypeError):
                seq_int_cp /= scalar

        # Bitwise operators
        for op in ('__lshift__', '__rshift__', '__or__', '__and__', '__xor__'):
            _test_binary(op, seq_bool, [42, -3, True, 0], [seq_int, seq_bool, -seq_int])

            with pytest.raises(TypeError):
                _test_binary(op, seq_bool, [0.5], [])
            with pytest.raises(TypeError):
                _test_binary(op, seq, [], [seq])

        # Unary operators
        for op in ['__neg__', '__abs__']:
            _test_unary(op, seq)
            _test_unary(op, -seq)
            _test_unary(op, seq_int)
            _test_unary(op, -seq_int)

        _test_unary('__abs__', seq_bool)
        _test_unary('__invert__', seq_bool)
        with pytest.raises(TypeError):
            _test_unary('__invert__', seq)

        # Restore flags.
        np.seterr(**flags)

    def test_arraysequence_repr(self):
        # Test that calling repr on a ArraySequence object is not falling.
        repr(SEQ_DATA['seq'])

        # Test calling repr when the number of arrays is bigger dans Numpy's
        # print option threshold.
        nb_arrays = 50
        seq = ArraySequence(generate_data(nb_arrays, common_shape=(1,), rng=SEQ_DATA['rng']))

        bkp_threshold = np.get_printoptions()['threshold']
        np.set_printoptions(threshold=nb_arrays * 2)
        txt1 = repr(seq)
        np.set_printoptions(threshold=nb_arrays // 2)
        txt2 = repr(seq)
        assert len(txt2) < len(txt1)
        np.set_printoptions(threshold=bkp_threshold)

    def test_save_and_load_arraysequence(self):
        # Test saving and loading an empty ArraySequence.
        with tempfile.TemporaryFile(mode='w+b', suffix='.npz') as f:
            seq = ArraySequence()
            seq.save(f)
            f.seek(0, os.SEEK_SET)
            loaded_seq = ArraySequence.load(f)
            assert_array_equal(loaded_seq._data, seq._data)
            assert_array_equal(loaded_seq._offsets, seq._offsets)
            assert_array_equal(loaded_seq._lengths, seq._lengths)

        # Test saving and loading a ArraySequence.
        with tempfile.TemporaryFile(mode='w+b', suffix='.npz') as f:
            seq = SEQ_DATA['seq']
            seq.save(f)
            f.seek(0, os.SEEK_SET)
            loaded_seq = ArraySequence.load(f)
            assert_array_equal(loaded_seq._data, seq._data)
            assert_array_equal(loaded_seq._offsets, seq._offsets)
            assert_array_equal(loaded_seq._lengths, seq._lengths)

            # Make sure we can add new elements to it.
            loaded_seq.append(SEQ_DATA['data'][0])

    def test_get_data(self):
        seq_view = SEQ_DATA['seq'][::2]
        check_arr_seq_view(seq_view, SEQ_DATA['seq'])

        # We make sure the array sequence data does not
        # contain more elements than it is supposed to.
        data = seq_view.get_data()
        assert len(data) < len(seq_view._data)


def test_concatenate():
    seq = SEQ_DATA['seq'].copy()  # In case there is in-place modification.
    seqs = [seq[:, [i]] for i in range(seq.common_shape[0])]
    new_seq = concatenate(seqs, axis=1)
    seq._data += 100  # Modifying the 'seq' shouldn't change 'new_seq'.
    check_arr_seq(new_seq, SEQ_DATA['data'])
    assert new_seq._is_view is not True

    seq = SEQ_DATA['seq']
    seqs = [seq[:, [i]] for i in range(seq.common_shape[0])]
    new_seq = concatenate(seqs, axis=0)
    assert len(new_seq) == seq.common_shape[0] * len(seq)
    assert_array_equal(new_seq._data, seq._data.T.reshape((-1, 1)))
