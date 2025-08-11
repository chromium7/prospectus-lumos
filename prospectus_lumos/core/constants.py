from __future__ import annotations

from django.db.models import IntegerChoices


class Months(IntegerChoices):
    JANUARY = 1, "January"
    FEBRUARY = 2, "February"
    MARCH = 3, "March"
    APRIL = 4, "April"
    MAY = 5, "May"
    JUNE = 6, "June"
    JULY = 7, "July"
    AUGUST = 8, "August"
    SEPTEMBER = 9, "September"
    OCTOBER = 10, "October"
    NOVEMBER = 11, "November"
    DECEMBER = 12, "December"


MONTHS_LIST = [
    (Months.JANUARY, "January"),
    (Months.FEBRUARY, "February"),
    (Months.MARCH, "March"),
    (Months.APRIL, "April"),
    (Months.MAY, "May"),
    (Months.JUNE, "June"),
    (Months.JULY, "July"),
    (Months.AUGUST, "August"),
    (Months.SEPTEMBER, "September"),
    (Months.OCTOBER, "October"),
    (Months.NOVEMBER, "November"),
    (Months.DECEMBER, "December"),
]
