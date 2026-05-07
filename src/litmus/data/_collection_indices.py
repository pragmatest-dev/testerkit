"""Runner-neutral assignment of sequence-relative step + vector indices.

The pytest plugin extracts ``(module, class_name, function)`` from each
collected item; this module turns that list into the ``step_index`` /
``vector_index`` / ``vector_count_planned`` triples consumers need.  The
algorithm is identical for any runner that produces an ordered list of
test executions grouped by logical step — only the per-runner attribute
extraction stays in the runner-specific plugin.

* ``step_index`` is **sequence-relative**: it resets per parent (root
  vs. class), and all variants of the same logical step share one value.
* ``vector_index`` is the 0-based position within the sweep expansion
  for that step.
* ``vector_count_planned`` records the manifest's intent so consumers
  can detect unrun vectors after the run.
"""

from __future__ import annotations

from collections import defaultdict

# Identity key for a logical step: (module_name, class_name, function_name).
# Empty strings stand in for missing components so the tuple is hashable
# and ordered consistently.
StepKey = tuple[str, str, str]


def assign_indices(keys: list[StepKey]) -> list[tuple[int, int, int]]:
    """Compute ``(step_index, vector_index, vector_count_planned)`` per key.

    Caller-supplied ``keys`` must already be in execution order — the
    function preserves order and assigns indices accordingly.

    Args:
        keys: One :data:`StepKey` per item, in collection / execution order.
            The same step running multiple sweep variants appears N times.

    Returns:
        A list of triples, parallel to ``keys``.  Variants of the same
        logical step share ``step_index`` and differ only in
        ``vector_index``.
    """
    # First pass: count occurrences of each key for vector_count_planned.
    group_count: dict[StepKey, int] = defaultdict(int)
    for key in keys:
        group_count[key] += 1

    # Second pass: walk in order assigning step_index per parent and
    # vector_index per key. The "parent" partition is by class_name (the
    # second element of the key) — root-level functions share class_name=""
    # so they count together; class methods count within their class.
    parent_step_index: dict[str, dict[StepKey, int]] = defaultdict(dict)
    seen_in_group: dict[StepKey, int] = {}

    out: list[tuple[int, int, int]] = []
    for key in keys:
        parent = key[1]  # class_name (or "" for root)
        bucket = parent_step_index[parent]
        if key not in bucket:
            bucket[key] = len(bucket)
        step_idx = bucket[key]

        vec_idx = seen_in_group.get(key, 0)
        seen_in_group[key] = vec_idx + 1

        out.append((step_idx, vec_idx, group_count[key]))
    return out
