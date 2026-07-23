# Local backend

The FastAPI backend is organized by dependency direction:

```text
api -> services -> contracts/domain
          |
          v
 providers / infrastructure
```

Keep provider wire formats inside `providers/`. Audience selection, speaking timing, model-call grouping and memory extraction remain open algorithms and must not be embedded in transport handlers.
