"""The operation layer: the part that keeps the pipeline running.

The lower layers already hold a fenced queue, a deterministic scheduler, a
lease fence, durable checkpoints, a transactional outbox, a supervised worker,
a file lock and a budget model. This package composes them into a process
that runs on its own, keeps itself inside its bounds, and says plainly why it
stopped. It writes no second queue, no second lock and no second budget.
"""
