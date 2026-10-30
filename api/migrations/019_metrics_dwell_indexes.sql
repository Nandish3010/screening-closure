-- M7-8: the median days per transition (GET /metrics/closure) reads the middle one or two rows of each transition in value order.
-- With these indexes that is an index walk to the middle (OFFSET) per transition, not a sort of every dwell row: at 1,000,000 cases
-- (4,075,179 dwell rows) the sort took 6.8 s, the walk is a fraction of a second (docs/results/scale.md).
CREATE INDEX case_metric_dwells_days_idx ON case_metric_dwells (frm, to_state, days);
CREATE INDEX case_metric_dwells_wdays_idx ON case_metric_dwells (frm, to_state, wdays);
