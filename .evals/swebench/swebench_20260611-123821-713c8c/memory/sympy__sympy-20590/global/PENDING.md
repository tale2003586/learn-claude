# Pending Memory


- [decision] When a class inherits from a mixin without __slots__, subclasses with __slots__ may get __dict__. Add __slots__ = () to the mixin to prevent automatic dict creation. (source: `task:coding-f04f62d3/llm`)

- [project] Basic inherits from Printable (alias DefaultPrinting). Printable lacked __slots__, causing Basic subclasses like Symbol to gain __dict__. (source: `task:coding-f04f62d3/llm`)

- [fact] EvalfMixin already defined __slots__ = () to avoid similar issues with __dict__ leakage. (source: `task:coding-f04f62d3/llm`)

- [preference] Prefer minimal patches: adding __slots__ = () to a base class is a two-line change that restores intended behavior without side effects. (source: `task:coding-f04f62d3/llm`)
