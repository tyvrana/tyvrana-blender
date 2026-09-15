"""Bounded resource selection before expensive summary construction."""

from collections.abc import Callable, Iterable

from .models import InspectArguments, PageInfo


def page[T](
    items: Iterable[T], query: InspectArguments, name: Callable[[T], str]
) -> tuple[list[T], PageInfo]:
    all_items = list(items)
    names = set(query.names) if query.names is not None else None
    matched = sorted(
        (
            item
            for item in all_items
            if (names is None or name(item) in names)
            and name(item).startswith(query.prefix)
        ),
        key=name,
    )
    end = query.offset + query.limit
    selected = matched[query.offset : end]
    return selected, PageInfo(
        total_count=len(all_items),
        matched_count=len(matched),
        offset=query.offset,
        returned_count=len(selected),
        next_offset=end if end < len(matched) else None,
    )
