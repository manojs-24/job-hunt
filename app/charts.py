"""Altair charts for the dashboard.

One series per chart, so every bar uses the same single blue (categorical slot 1): bars are
thin with a rounded data end, the grid is a recessive hairline, and there is no legend because
the card title names the series. Colours differ for light and dark so each is checked against
its own background.
"""
import altair as alt
import pandas as pd
import streamlit as st

PALETTES = {
    "light": {"mark": "#2a78d6", "label": "#52514e", "muted": "#898781", "grid": "#e1e0d9", "axis": "#c3c2b7"},
    "dark": {"mark": "#3987e5", "label": "#c3c2b7", "muted": "#898781", "grid": "#2c2c2a", "axis": "#383835"},
}
COLUMN_HEIGHT = 240


def palette() -> dict:
    try:
        mode = st.context.theme.type
    except Exception:  # not available outside a running app; the app's own default is dark
        mode = None
    return PALETTES["light" if mode == "light" else "dark"]


def _finish(chart: alt.Chart, p: dict, height: int) -> alt.Chart:
    return (
        chart.properties(height=height, width="container")
        .configure(background="transparent")
        .configure_view(stroke=None)
        .configure_axis(labelColor=p["muted"], titleColor=p["label"], gridColor=p["grid"], gridWidth=1,
                        domainColor=p["axis"], tickColor=p["axis"], labelFontSize=12, titleFontSize=12)
    )


def column_chart(df: pd.DataFrame) -> alt.Chart:
    """Vertical bars: category (first column) against jobs (second column), in the order given."""
    p = palette()
    cat, val = df.columns[0], df.columns[1]
    mark = {"size": 28} if len(df) < 12 else {}          # many bars: let the band set the width
    chart = alt.Chart(df).mark_bar(color=p["mark"], cornerRadiusEnd=4, **mark).encode(
        x=alt.X(f"{cat}:N", sort=list(df[cat]), title=None,
                axis=alt.Axis(labelAngle=0, labelOverlap="parity", ticks=False, labelColor=p["label"])),
        y=alt.Y(f"{val}:Q", title=None, axis=alt.Axis(tickMinStep=1, format="d", ticks=False, domain=False)),
        tooltip=[alt.Tooltip(f"{cat}:N", title=cat), alt.Tooltip(f"{val}:Q", title="Jobs")],
    )
    return _finish(chart, p, COLUMN_HEIGHT)


def ranked_bars(df: pd.DataFrame) -> alt.Chart:
    """Horizontal bars, largest first: names read easily and bars share one baseline."""
    p = palette()
    cat, val = df.columns[0], df.columns[1]
    chart = alt.Chart(df).mark_bar(color=p["mark"], cornerRadiusEnd=4, size=16).encode(
        y=alt.Y(f"{cat}:N", sort=list(df[cat]), title=None,
                axis=alt.Axis(ticks=False, domain=False, labelColor=p["label"], labelLimit=220)),
        x=alt.X(f"{val}:Q", title=None, axis=alt.Axis(tickMinStep=1, format="d", ticks=False, domain=False)),
        tooltip=[alt.Tooltip(f"{cat}:N", title=cat), alt.Tooltip(f"{val}:Q", title="Jobs")],
    )
    return _finish(chart, p, 34 * len(df) + 28)
