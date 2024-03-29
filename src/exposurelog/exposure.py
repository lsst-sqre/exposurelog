__all__ = ["Exposure", "EXPOSURE_ORDER_BY_VALUES"]

import datetime

import pydantic


class Exposure(pydantic.BaseModel):
    obs_id: str = pydantic.Field(
        description="Observation ID. Note that the Rubin obs_id format "
        "includes sections for day_obs and seq_num, but these are also "
        "available as separate database columns, for convenience."
    )
    id: int = pydantic.Field(description="Integer derived from 'id'.")
    instrument: str = pydantic.Field(
        description="Short name of instrument, e.g. LSSTCam."
    )
    observation_type: str = pydantic.Field(
        description="The observation type of this exposure "
        "(e.g. dark, bias, science)."
    )
    observation_reason: str = pydantic.Field(
        description="The reason this observation was taken. "
        "(e.g. science, filter scan, unknown)."
    )
    day_obs: int = pydantic.Field(
        description="Observation day, as an integer in the form YYYYMMDD."
    )
    seq_num: int = pydantic.Field(
        description="Counter for the observation within a larger sequence."
    )
    group_name: str = pydantic.Field(
        description="String group identifier associated with this exposure "
        "by the acquisition system."
    )
    target_name: str = pydantic.Field(
        description="Object of interest for this observation or survey field name."
    )
    science_program: str = pydantic.Field(
        description="Observing program (survey, proposal, engineering project)."
    )
    tracking_ra: None | float = pydantic.Field(
        None,
        description="Tracking ICRS right ascension of boresight in degrees. "
        "Can be None for observations that are not on sky.",
    )
    tracking_dec: None | float = pydantic.Field(
        None,
        description="Tracking ICRS declination of boresight in degrees. "
        "Can be None for observations that are not on sky.",
    )
    sky_angle: None | float = pydantic.Field(
        None,
        description="Angle of the instrument focal plane on the sky in degrees. "
        "Can  be NULL for observations that are not on sky, or for "
        "where the sky angle changes during the observation.",
    )
    timespan_begin: None | datetime.datetime = pydantic.Field(
        description="Start TAI time of observation, or None if unknown. "
        "The date ought to always be known, but we have seen cases where it is not."
    )
    timespan_end: None | datetime.datetime = pydantic.Field(
        description="End TAI time of observation, or None if unknown. "
        "The date ought to always be known, but we have seen cases where it is not."
    )


def _make_exposure_order_by_values() -> tuple[str, ...]:
    """Make a tuple of valid order_by values for find_messages.

    Return a tuple of all field names except "instrument",
    plus those same field names with a leading "-".
    """
    omit_fields = {"instrument"}

    order_by_fields = []
    for field in Exposure.model_json_schema()["properties"]:
        if field in omit_fields:
            continue
        order_by_fields += [field, "-" + field]
    return tuple(order_by_fields)


# Tuple of valid order_by values.
# Each of these exists in the Exposure class.
EXPOSURE_ORDER_BY_VALUES = _make_exposure_order_by_values()
