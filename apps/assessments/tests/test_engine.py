from decimal import Decimal as D

import pytest

from apps.assessments import engine
from apps.assessments.engine import Category, Item, Mark, Scale

OUT_OF_20 = Scale()


def mark(categories, items, marks, scale=OUT_OF_20, **options):
    return engine.to_scale(engine.subject_fraction(categories, items, marks, **options), scale)


class TestOneCategory:
    def test_average_of_percentages(self):
        # Quiz 15/20 (75 %) and quiz 8/10 (80 %): 77.5 % → 15.50 / 20.
        items = [Item(1, 10, D(20)), Item(2, 10, D(10))]
        marks = {1: Mark(D(15)), 2: Mark(D(8))}
        assert mark([Category(10, D(1))], items, marks) == D("15.50")

    def test_total_of_points(self):
        # The same marks added up: 23 points out of 30 → 15.33 / 20.
        items = [Item(1, 10, D(20)), Item(2, 10, D(10))]
        marks = {1: Mark(D(15)), 2: Mark(D(8))}
        assert mark([Category(10, D(1), engine.TOTAL)], items, marks) == D("15.33")

    def test_assessment_weights_inside_a_category(self):
        # A test that counts double: (10/20 × 1 + 20/20 × 2) / 3 → 16.67.
        items = [Item(1, 10, D(20)), Item(2, 10, D(20), weight=D(2))]
        marks = {1: Mark(D(10)), 2: Mark(D(20))}
        assert mark([Category(10, D(1))], items, marks) == D("16.67")


class TestCategories:
    def test_class_work_and_composition_like_in_guinea(self):
        # Moyenne = (moyenne de classe + 2 × composition) / 3 = (12 + 2 × 15) / 3 = 14.
        categories = [Category(1, D(1)), Category(2, D(2))]
        items = [Item(10, 1, D(20)), Item(11, 1, D(20)), Item(20, 2, D(20))]
        marks = {10: Mark(D(10)), 11: Mark(D(14)), 20: Mark(D(15))}
        assert mark(categories, items, marks) == D("14.00")

    def test_percentage_weights_out_of_100_like_in_liberia(self):
        # Quizzes 20 %, assignments 20 %, tests 20 %, exam 40 %, reported out of 100.
        categories = [Category(1, D(20)), Category(2, D(20)), Category(3, D(20)), Category(4, D(40))]
        items = [Item(10, 1, D(10)), Item(20, 2, D(25)), Item(30, 3, D(50)), Item(40, 4, D(100))]
        marks = {10: Mark(D(9)), 20: Mark(D(20)), 30: Mark(D(35)), 40: Mark(D(72))}
        # 0.2 × 90 + 0.2 × 80 + 0.2 × 70 + 0.4 × 72 = 76.8
        assert mark(categories, items, marks, Scale(max_mark=D(100), pass_mark=D(70), decimals=1)) == D(
            "76.8"
        )

    def test_a_category_not_marked_yet_is_left_out(self):
        # Mid-term: no exam yet, so the class work alone gives the mark.
        categories = [Category(1, D(1)), Category(2, D(2))]
        items = [Item(10, 1, D(20)), Item(20, 2, D(20))]
        assert mark(categories, items, {10: Mark(D(13))}) == D("13.00")

    def test_nothing_counts_yet(self):
        categories = [Category(1, D(1))]
        assert mark(categories, [Item(10, 1, D(20))], {}) is None
        assert mark(categories, [], {}) is None
        assert mark([], [], {}) is None


class TestMissingAndExcused:
    categories = [Category(1, D(1))]
    items = [Item(10, 1, D(20)), Item(11, 1, D(20)), Item(12, 1, D(20))]

    def test_missing_marks_are_left_out_by_default(self):
        assert mark(self.categories, self.items, {10: Mark(D(16))}, started={10, 11}) == D("16.00")

    def test_missing_marks_count_as_zero_when_the_teacher_says_so(self):
        # 11 was marked for other students: zero. 12 has not been marked for anyone yet: still left out.
        result = mark(
            self.categories,
            self.items,
            {10: Mark(D(16))},
            missing_policy=engine.COUNT_AS_ZERO,
            started={10, 11},
        )
        assert result == D("8.00")

    def test_an_excused_student_is_never_counted_as_zero(self):
        marks = {10: Mark(D(16)), 11: Mark(excused=True)}
        result = mark(
            self.categories, self.items, marks, missing_policy=engine.COUNT_AS_ZERO, started={10, 11}
        )
        assert result == D("16.00")

    def test_a_real_zero_counts(self):
        assert mark(self.categories, self.items, {10: Mark(D(16)), 11: Mark(D(0))}) == D("8.00")


class TestRounding:
    @pytest.mark.parametrize(("decimals", "expected"), [(0, D("67")), (1, D("66.7")), (2, D("66.67"))])
    def test_half_up_to_the_school_rule(self, decimals, expected):
        scale = Scale(max_mark=D(100), pass_mark=D(50), decimals=decimals)
        assert mark([Category(1, D(1))], [Item(1, 1, D(3))], {1: Mark(D(2))}, scale) == expected

    def test_exact_half_rounds_up(self):
        assert engine.round_mark(D("12.345"), 2) == D("12.35")
        assert engine.round_mark(D("12.5"), 0) == D("13")


class TestRanks:
    values = {"a": D("15"), "b": D("17"), "c": D("15"), "d": D("12"), "e": None}

    def test_competition_ranking_skips_after_ties(self):
        assert engine.ranks(self.values, engine.COMPETITION) == {"b": 1, "a": 2, "c": 2, "d": 4}

    def test_dense_ranking_does_not_skip(self):
        assert engine.ranks(self.values, engine.DENSE) == {"b": 1, "a": 2, "c": 2, "d": 3}

    def test_nobody_to_rank(self):
        assert engine.ranks({"a": None}) == {}


class TestOverall:
    def test_coefficient_weighted_average(self):
        # Maths 14 × 4, French 11 × 3, Sport 18 × 1 → (56 + 33 + 18) / 8 = 13.375 → 13.38.
        assert engine.overall_average([(D(14), D(4)), (D(11), D(3)), (D(18), D(1))], 2) == D("13.38")

    def test_subjects_without_a_mark_are_left_out(self):
        assert engine.overall_average([(D(14), D(4)), (None, D(3))], 2) == D("14.00")
        assert engine.overall_average([(None, D(3))], 2) is None

    def test_stats(self):
        s = engine.stats([D(8), D(12), D(16), None], Scale())
        assert (s.average, s.lowest, s.highest, s.passed, s.counted) == (D("12.00"), D(8), D(16), 2, 3)
        assert engine.stats([None], Scale()).average is None
