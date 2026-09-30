"""Amounts written out in words, as receipts state them ("Arrêté le présent reçu à la somme de ...").

French follows the traditional spelling (vingt et un, quatre-vingts, deux cent mille); English is American
(no "and" after hundred).
"""

from decimal import Decimal

from .money import minor_places, to_money

FR_UNITS = [
    "zéro", "un", "deux", "trois", "quatre", "cinq", "six", "sept", "huit", "neuf", "dix",
    "onze", "douze", "treize", "quatorze", "quinze", "seize", "dix-sept", "dix-huit", "dix-neuf",
]  # fmt: skip
FR_TENS = {2: "vingt", 3: "trente", 4: "quarante", 5: "cinquante", 6: "soixante"}

EN_UNITS = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen",
]  # fmt: skip
EN_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]

# (singular, plural) of the main unit and of the minor unit.
CURRENCY_NAMES = {
    "fr": {
        "GNF": (("franc guinéen", "francs guinéens"), None),
        "XOF": (("franc CFA", "francs CFA"), None),
        "XAF": (("franc CFA", "francs CFA"), None),
        "LRD": (("dollar libérien", "dollars libériens"), ("cent", "cents")),
        "USD": (("dollar américain", "dollars américains"), ("cent", "cents")),
        "EUR": (("euro", "euros"), ("centime", "centimes")),
    },
    "en": {
        "GNF": (("Guinean franc", "Guinean francs"), None),
        "XOF": (("CFA franc", "CFA francs"), None),
        "XAF": (("CFA franc", "CFA francs"), None),
        "LRD": (("Liberian dollar", "Liberian dollars"), ("cent", "cents")),
        "USD": (("US dollar", "US dollars"), ("cent", "cents")),
        "EUR": (("euro", "euros"), ("cent", "cents")),
    },
}


def _fr_below_100(n: int) -> str:
    if n < 20:
        return FR_UNITS[n]
    tens, unit = divmod(n, 10)
    if tens == 7:
        return "soixante et onze" if unit == 1 else f"soixante-{FR_UNITS[10 + unit]}"
    if tens == 8:
        return "quatre-vingts" if unit == 0 else f"quatre-vingt-{FR_UNITS[unit]}"
    if tens == 9:
        return f"quatre-vingt-{FR_UNITS[10 + unit]}"
    if unit == 0:
        return FR_TENS[tens]
    return f"{FR_TENS[tens]} et un" if unit == 1 else f"{FR_TENS[tens]}-{FR_UNITS[unit]}"


def _fr_below_1000(n: int, *, before_mille: bool = False) -> str:
    """`before_mille`: "cent" and "quatre-vingt" take no s before "mille" (deux cent mille)."""
    hundreds, rest = divmod(n, 100)
    parts = []
    if hundreds == 1:
        parts.append("cent")
    elif hundreds:
        parts.append(f"{FR_UNITS[hundreds]} cent" + ("s" if rest == 0 and not before_mille else ""))
    if rest:
        words = _fr_below_100(rest)
        parts.append(words[:-1] if before_mille and words == "quatre-vingts" else words)
    return " ".join(parts)


def french(n: int) -> str:
    if n == 0:
        return FR_UNITS[0]
    billions, rest = divmod(n, 10**9)
    millions, rest = divmod(rest, 10**6)
    thousands, rest = divmod(rest, 1000)
    parts = []
    if billions:
        parts.append(f"{french(billions)} milliard{'s' if billions > 1 else ''}")
    if millions:
        parts.append(f"{_fr_below_1000(millions)} million{'s' if millions > 1 else ''}")
    if thousands:
        parts.append("mille" if thousands == 1 else f"{_fr_below_1000(thousands, before_mille=True)} mille")
    if rest:
        parts.append(_fr_below_1000(rest))
    return " ".join(parts)


def _en_below_1000(n: int) -> str:
    hundreds, rest = divmod(n, 100)
    parts = [f"{EN_UNITS[hundreds]} hundred"] if hundreds else []
    if rest >= 20:
        tens, unit = divmod(rest, 10)
        parts.append(EN_TENS[tens] + (f"-{EN_UNITS[unit]}" if unit else ""))
    elif rest:
        parts.append(EN_UNITS[rest])
    return " ".join(parts)


def english(n: int) -> str:
    if n == 0:
        return EN_UNITS[0]
    parts = []
    for value, name in ((10**9, "billion"), (10**6, "million"), (1000, "thousand")):
        count, n = divmod(n, value)
        if count:
            parts.append(f"{english(count) if count >= 1000 else _en_below_1000(count)} {name}")
    if n:
        parts.append(_en_below_1000(n))
    return " ".join(parts)


def amount_in_words(amount: Decimal, currency: str, language: str) -> str:
    """ "Deux millions cinq cent mille francs guinéens" / "One thousand two hundred fifty Liberian dollars and
    fifty cents". Unknown currencies keep their code."""
    language = "en" if language == "en" else "fr"
    amount = to_money(amount, currency)
    places = minor_places(currency)
    whole = int(amount)
    minor = int((amount - whole) * 10**places) if places else 0
    spell = english if language == "en" else french
    main_names, minor_names = CURRENCY_NAMES[language].get(currency.upper(), ((currency, currency), None))

    words = spell(whole)
    # French puts "de" between a round million or billion and the currency: "deux millions de francs".
    round_millions = words.endswith(("million", "millions", "milliard", "milliards"))
    joiner = " de " if language == "fr" and round_millions else " "
    text = f"{words}{joiner}{main_names[0] if whole == 1 else main_names[1]}"
    if minor:
        minor_name = (minor_names or ("cent", "cents"))[0 if minor == 1 else 1]
        text += f" {'and' if language == 'en' else 'et'} {spell(minor)} {minor_name}"
    return text[:1].upper() + text[1:]
