from __future__ import annotations

__all__ = ["delete_message"]

import astropy.time
import fastapi
import sqlalchemy as sa

from ..shared_state import SharedState, get_shared_state

router = fastapi.APIRouter()


@router.delete("/messages/{id}", status_code=204)
async def delete_message(
    id: str,
    state: SharedState = fastapi.Depends(get_shared_state),
) -> None:
    """Delete a message by marking it invalid.

    A no-op if already the message is already marked invalid.

    If the message is valid: set ``is_valid`` false and ``date_invalidated``
    to the current date.
    """
    current_tai = astropy.time.Time.now().tai.iso

    el_table = state.exposurelog_db.table

    # Delete the message by setting date_invalidated to the current TAI time
    # (if not already set). Note: coalesce returns the first non-null
    # value from a list of values.
    async with state.exposurelog_db.engine.acquire() as connection:
        result_proxy = await connection.execute(
            el_table.update()
            .where(el_table.c.id == id)
            .values(
                date_invalidated=sa.func.coalesce(
                    el_table.c.date_invalidated, current_tai
                )
            )
            .returning(el_table.c.is_valid)
        )
        rows = []
        async for row in result_proxy:
            rows.append(row)

    if len(rows) == 0:
        raise fastapi.HTTPException(
            status_code=404,
            detail=f"No message found with id={id}",
        )
    return None
