import pytest
from revoloco.mod import Mod, _normalize


@pytest.fixture
def mod():
    return Mod()


class TestForward:
    def test_integer_inputs(self, mod):
        assert mod.forward(2, 3) == 5

    def test_float_inputs(self, mod):
        assert mod.forward(1.5, 2.5) == 4.0

    def test_result_simplifies_to_int(self, mod):
        assert mod.forward(1.0, 2.0) == 3
        assert isinstance(mod.forward(1.0, 2.0), int)

    def test_non_numeric_raises(self, mod):
        with pytest.raises(ValueError):
            mod.forward("x", 1)


class TestAdd:
    def test_integer_inputs(self, mod):
        assert mod.add(3, 4) == 7

    def test_float_inputs(self, mod):
        assert mod.add(1.5, 2.5) == 4.0

    def test_result_simplifies_to_int(self, mod):
        assert mod.add(1.0, 2.0) == 3
        assert isinstance(mod.add(1.0, 2.0), int)

    def test_non_numeric_raises(self, mod):
        with pytest.raises(ValueError):
            mod.add("x", 1)


class TestSubtract:
    def test_integer_inputs(self, mod):
        assert mod.subtract(5, 3) == 2

    def test_float_inputs(self, mod):
        assert mod.subtract(5.5, 2.5) == 3.0

    def test_result_simplifies_to_int(self, mod):
        assert mod.subtract(4.0, 2.0) == 2
        assert isinstance(mod.subtract(4.0, 2.0), int)

    def test_non_numeric_raises(self, mod):
        with pytest.raises(ValueError):
            mod.subtract("x", 1)


class TestMultiply:
    def test_integer_inputs(self, mod):
        assert mod.multiply(3, 4) == 12

    def test_float_inputs(self, mod):
        assert mod.multiply(2.5, 2.0) == 5.0

    def test_result_simplifies_to_int(self, mod):
        assert mod.multiply(2.0, 3.0) == 6
        assert isinstance(mod.multiply(2.0, 3.0), int)

    def test_non_numeric_raises(self, mod):
        with pytest.raises(ValueError):
            mod.multiply("x", 1)


class TestDivide:
    def test_integer_inputs_exact(self, mod):
        assert mod.divide(10, 2) == 5
        assert isinstance(mod.divide(10, 2), int)

    def test_integer_inputs_fractional(self, mod):
        assert mod.divide(10, 4) == 2.5
        assert isinstance(mod.divide(10, 4), float)

    def test_float_inputs(self, mod):
        assert mod.divide(7.5, 2.5) == 3.0

    def test_division_by_zero_raises(self, mod):
        with pytest.raises(ValueError, match="division by zero"):
            mod.divide(5, 0)

    def test_non_numeric_raises(self, mod):
        with pytest.raises(ValueError):
            mod.divide("x", 1)


class TestNormalize:
    def test_whole_float_becomes_int(self):
        assert _normalize(3.0) == 3
        assert isinstance(_normalize(3.0), int)

    def test_true_float_passthrough(self):
        assert _normalize(2.5) == 2.5
        assert isinstance(_normalize(2.5), float)


class TestCoerce:
    def test_string_a_raises(self, mod):
        with pytest.raises(ValueError):
            mod.forward("x", 1)

    def test_string_b_raises(self, mod):
        with pytest.raises(ValueError):
            mod.forward(1, "y")

    def test_none_raises(self, mod):
        with pytest.raises(ValueError):
            mod.forward(None, 1)
