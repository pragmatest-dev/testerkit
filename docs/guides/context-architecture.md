# Context Architecture

**Understanding hierarchical context in Litmus**

## Overview

The `Context` is Litmus's central data structure for test execution. It provides **hierarchical scoping** with three levels:

```
Run Context (session-wide)
  ├─ Step Context (per test function)
  │    ├─ Vector Context (per test iteration)
  │    ├─ Vector Context
  │    └─ Vector Context
  ├─ Step Context
  │    └─ Vector Context
  ...
```

Data set at a parent level is **automatically inherited** by children. Children can **override** parent values locally without affecting siblings.

## The Context Class

### Core Structure

```python
class Context:
    def __init__(self, parent: Context | None = None, prev: Context | None = None):
        """Initialize context with optional parent for inheritance.

        Args:
            parent: Parent context to inherit values from.
            prev: Previous sibling context (for change detection across vectors).
        """
        self._parent = parent
        self._prev = prev
        self._inputs: dict[str, Any] = {}
        self._outputs: dict[str, Any] = {}
```

### Two Types of Data

| Type | Methods | Storage | Purpose |
|------|---------|---------|---------|
| **Inputs** | `configure()`, `set_in()` | `_inputs` dict | Commanded values, setpoints → `in_*` columns |
| **Outputs** | `observe()`, `set_out()` | `_outputs` dict | Observations, environmental data → `out_*` columns |

Both inherit from parent contexts using the same lookup chain.

## Value Lookup (Inheritance)

When you call `context.get_in("key")`, Litmus searches:

1. **This context's inputs** (`_inputs`)
2. **Parent's inputs** (if parent exists)
3. **Grandparent's inputs** (recursively up the chain)
4. **Default value** (if provided)

```python
def get_in(self, key: str, default: Any = None) -> Any:
    """Get input value with inheritance."""
    # Check local inputs first
    if key in self._inputs:
        return self._inputs[key]

    # Check parent if available
    if self._parent:
        return self._parent.get_in(key, default)

    # Return default if not found
    return default
```

The same logic applies to `get_out()` for outputs.

## Lifecycle: How Contexts Are Created

### 1. Run Context (Session Start)

Created once when the test session starts:

```python
# In TestHarness.__init__
self._run_context = Context()  # No parent, root of hierarchy
```

The run context lives for the entire test session. Any data set here is visible to all steps and vectors.

**Example:**
```python
def test_setup(run_context):
    # Set once, visible everywhere
    run_context.configure("fixture_serial", "FIX-001")
    run_context.observe("lab_temp", 23.5)
```

### 2. Step Context (Per Test Function)

Created when a test step begins:

```python
# In TestHarness.start_step()
self._step_context = self._run_context.child()
```

The step context:
- **Inherits** from run context
- **Persists** across all vectors in that step
- Is **cleared** when the step ends

**Example:**
```python
@litmus_test
def test_voltage_sweep(context, psu, dmm):
    # This context is the STEP context
    # It sees run_context values but can override them
    context.configure("test_type", "voltage_sweep")

    # Runs multiple times (vectors), step context persists
    return dmm.measure_voltage()
```

### 3. Vector Context (Per Test Iteration)

Created for each test vector:

```python
# In TestHarness.run_vector()
parent_context = self._step_context or self._run_context
self._vector_context = Context(parent=parent_context, prev=self._prev_vector_context)

# Pre-populate with vector params
self._vector_context.set_inputs(vector.params())
```

The vector context:
- **Inherits** from step context (which inherits from run context)
- **Pre-populated** with vector parameters from config.yaml
- **Fresh** for each vector iteration
- **Links** to previous vector via `_prev` for change detection

**Example:**
```python
@litmus_test
def test_sweep(context, psu):
    # context is the VECTOR context
    # It has vector params + step data + run data

    vin = context.inputs["vin"]  # From vector params
    fixture = context.get_in("fixture_serial")  # From run context
    test_type = context.get_in("test_type")  # From step context
```

### 4. Context Cleanup

After each vector:

```python
# Save current for next vector's change detection
self._prev_vector_context = self._vector_context
self._vector_context = None  # Clear for next vector
```

After each step:

```python
self._step_context = None  # Step ends, context cleared
```

## Inheritance Example

```python
# Session start
run_context.configure("fixture_serial", "FIX-001")
run_context.observe("lab_temp", 23.5)

# Step 1 starts
step1_context = run_context.child()
step1_context.configure("step_name", "test_voltage")

# Vector 1 of step 1
vector1_context = Context(parent=step1_context, prev=None)
vector1_context.set_inputs({"vin": 5.0, "load": 0.1})

# What can vector1_context see?
vector1_context.get_in("vin")  # 5.0 (from vector params)
vector1_context.get_in("load")  # 0.1 (from vector params)
vector1_context.get_in("step_name")  # "test_voltage" (from step)
vector1_context.get_in("fixture_serial")  # "FIX-001" (from run)
vector1_context.get_out("lab_temp")  # 23.5 (from run)

# Vector 2 of step 1
vector2_context = Context(parent=step1_context, prev=vector1_context)
vector2_context.set_inputs({"vin": 5.0, "load": 0.5})

# What can vector2_context see?
vector2_context.get_in("vin")  # 5.0
vector2_context.get_in("load")  # 0.5 (different from vector1)
vector2_context.get_in("step_name")  # "test_voltage" (inherited from step)
vector2_context.changed("vin")  # False (same as prev)
vector2_context.changed("load")  # True (different from prev)
```

## Change Detection

The `_prev` reference enables efficient change detection across vectors:

```python
def changed(self, key: str) -> bool:
    """Check if an input parameter changed from the previous vector."""
    if self._prev is None:
        return True  # First vector - everything is "changed"

    current_value = self.get_in(key)
    prev_value = self._prev.get_in(key)
    return current_value != prev_value
```

**Use case:** Optimize slow operations

```python
@litmus_test
def test_temp_sweep(context, chamber, psu, dmm):
    # Only reconfigure chamber when temperature changes
    if context.changed("temperature"):
        chamber.set_temp(context.inputs["temperature"])
        time.sleep(60)  # Soak time

    # Always reconfigure PSU (changes every vector)
    psu.set_voltage(context.inputs["vin"])
    return dmm.measure_voltage()
```

## The TestHarness Context Property

The harness provides a single `.context` property that returns the most specific active context:

```python
@property
def context(self) -> Context:
    """Current active context (vector > step > run)."""
    if self._vector_context is not None:
        return self._vector_context
    if self._step_context is not None:
        return self._step_context
    return self._run_context
```

This means calling `harness.context` always gives you the right scope:
- **During vector execution**: Returns vector context
- **During step (outside vector)**: Returns step context
- **During run setup**: Returns run context

## Data Flow to Parquet

When a vector completes, the harness snapshots the context:

```python
# In run_vector finally block
test_vector.params = self._vector_context.inputs      # All inputs → in_* columns
test_vector.observations = self._vector_context.outputs  # All outputs → out_* columns
```

The Parquet schema flattens this:

| Column | Source | Example |
|--------|--------|---------|
| `in_vin` | `context.inputs["vin"]` | 5.0 |
| `in_load` | `context.inputs["load"]` | 0.5 |
| `in_fixture_serial` | `context.inputs["fixture_serial"]` (from run) | "FIX-001" |
| `out_lab_temp` | `context.outputs["lab_temp"]` (from run) | 23.5 |
| `out_dut_temp` | `context.outputs["dut_temp"]` | 42.3 |

All inherited values are **flattened** into each vector's row, providing complete traceability.

## Common Patterns

### Pattern 1: Run-Level Configuration

Set once, see everywhere:

```python
def test_setup(run_context):
    """Set run-level values before any tests."""
    run_context.configure("operator_id", "ENG-123")
    run_context.configure("fixture_serial", "FIX-001")
    run_context.observe("lab_temp", read_temp_sensor())
```

### Pattern 2: Step-Level Setup

Configure per-test but shared across vectors:

```python
@litmus_test
def test_efficiency(context, psu, dmm):
    # First vector only - set step-level config
    if context.inputs.get("_index", 0) == 0:
        context.configure("test_start_time", time.time())

    # Runs for each vector
    return dmm.measure_voltage()
```

### Pattern 3: Vector-Level Data

Record per-iteration observations:

```python
@litmus_test
def test_with_probe(context, psu, dmm, temp_probe):
    # Vector-specific observation
    context.observe("dut_temp", temp_probe.read())

    # Vector params
    psu.set_voltage(context.inputs["vin"])
    return dmm.measure_voltage()
```

### Pattern 4: Override Parent Values

Child can override without affecting parent:

```python
# Run context has default
run_context.configure("timeout", 10.0)

# Step overrides for this test only
@litmus_test
def test_slow_measurement(context, dmm):
    context.configure("timeout", 60.0)  # Override run-level value
    # This doesn't affect other steps
```

### Pattern 5: Accessing Limits from Tests

Tests can access resolved limits through the context:

```python
@litmus_test
def test_adaptive_sampling(context, dmm):
    """Take more samples if limit is tight."""
    limit = context.get_limit("output_voltage")

    if limit and limit.tolerance_pct < 2.0:
        # Tight limit - take 10 samples and average
        samples = [dmm.measure_voltage() for _ in range(10)]
        return sum(samples) / len(samples)
    else:
        # Loose limit - single sample is fine
        return dmm.measure_voltage()
```

**When to use `get_limit()`:**

- **Adaptive behavior**: Adjust test based on limit tightness
- **Logging**: Record limit info in observations for analysis
- **Custom validation**: Implement domain-specific pass/fail logic
- **Debugging**: Display limit context during test development

**How it works:**

`context.get_limit(name)` uses the same resolution logic as `harness.measure()`:

1. Check `_limits` dict for explicit limits
2. Resolve callable limits with current context
3. Look up spec-driven limits from SpecContext
4. Return None if no limit defined

## Advanced: Direct TestHarness Usage

For test architects who need explicit control:

```python
def test_explicit_control(psu, dmm, litmus_logger):
    harness = TestHarness(
        config={"vectors": [{"load": 0.1}, {"load": 0.8}]},
        logger=litmus_logger,
    )

    # Run-level setup
    harness.context.configure("fixture_id", "FIX-001")

    # Start step (creates step context)
    with harness.start_step("my_step"):
        harness.context.configure("step_type", "efficiency")

        # Iterate vectors (each gets vector context)
        for vector in harness.vectors:
            with harness.run_vector(vector):
                # harness.context now points to vector context
                harness.context.observe("temp", 25.0)
                harness.measure("vout", dmm.measure_voltage())
```

## Key Takeaways

1. **Three-level hierarchy**: Run → Step → Vector
2. **Automatic inheritance**: Children see parent values
3. **Local override**: Children can shadow parent values
4. **Fresh vector contexts**: Each iteration gets a clean slate
5. **Change detection**: `_prev` link enables efficient `.changed()`
6. **Semantic separation**: `configure()` for inputs, `observe()` for outputs
7. **Complete traceability**: All inherited values stored in Parquet

The context architecture enables:
- **Clean separation** of run-wide, step-wide, and vector-specific data
- **No global state** - everything is scoped
- **Efficient reuse** - set once at run level, use everywhere
- **Full traceability** - every measurement has complete context

## See Also

- [Traceability Guide](traceability.md) - How context data flows to Parquet
- [Writing Tests](writing-tests.md) - Practical test patterns
- [Context API Reference](../reference/context-api.md) - Full API documentation
