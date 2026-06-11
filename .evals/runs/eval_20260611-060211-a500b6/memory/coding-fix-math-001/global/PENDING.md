# Pending Memory


- [decision] Corrected clamp function: return low when value < low, high when value > high. (source: `task:coding-1b3a8c12/llm`)

- [fact] Tests expect clamp to return low for below-range, high for above-range, value for inside. (source: `task:coding-1b3a8c12/llm`)

- [preference] Do not modify existing unit tests when fixing code. (source: `task:coding-1b3a8c12/llm`)
