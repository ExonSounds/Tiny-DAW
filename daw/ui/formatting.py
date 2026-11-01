"""Turning numbers into display text."""
from .. import config


def fmt_time(seconds, decimals=3):
    """75.5 -> '1:15.500'"""
    m, s = divmod(seconds, 60)
    width = 3 + decimals if decimals else 2
    return f"{int(m)}:{s:0{width}.{decimals}f}"


def fmt_bars(seconds, timing):
    """Bar.beat.part, e.g. '5.2.3' (part: 16th within the beat, or third in thirds view)."""
    return "{}.{}.{}".format(*timing.position(seconds))


def ruler_step(pps):
    """Seconds between ruler ticks so that ticks are at least RULER_MIN_TICK_PX apart."""
    return next((s for s in config.RULER_STEPS if s * pps >= config.RULER_MIN_TICK_PX), config.RULER_STEPS[-1])


def step_decimals(step):
    """Decimals needed to label ticks every `step` seconds: 0.25 -> 2, 0.5 -> 1, 2 -> 0."""
    return 0 if step >= 1 else len(f"{step:g}".split(".")[1])


def fmt_db(db, min_db):
    return "-inf dB" if db <= min_db else f"{db:+d} dB"
