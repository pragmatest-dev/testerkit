# TPS54302 — 3A, 28V Input, Synchronous Step-Down Converter

**Datasheet Excerpt — Electrical Characteristics**

Document Number: SLVSD12A
Revision: March 2024

---

## 1. Pin Configuration and Functions

| Pin | Name | Type | Description |
|-----|------|------|-------------|
| 1 | BOOT | Power | Bootstrap capacitor for high-side driver |
| 2 | VIN | Power | Input supply voltage |
| 3 | GND | Ground | Ground reference |
| 4 | EN | Input | Enable pin (active high) |
| 5 | SS/TR | I/O | Soft start and tracking |
| 6 | RT/CLK | Input | Oscillator frequency setting |
| 7 | PWRGD | Output | Power good indicator (open drain) |
| 8 | VSENSE | Input | Feedback voltage sense |
| 9 | COMP | Output | Error amplifier output |
| 10 | SW | Power | Switching node output |

---

## 2. Absolute Maximum Ratings

| Parameter | Min | Max | Unit |
|-----------|-----|-----|------|
| VIN to GND | -0.3 | 30 | V |
| SW to GND | -0.6 | 30 | V |
| EN to GND | -0.3 | 7 | V |
| VSENSE to GND | -0.3 | 3.6 | V |
| Operating junction temperature | -40 | 150 | °C |
| Storage temperature | -65 | 150 | °C |

---

## 3. Recommended Operating Conditions

| Parameter | Min | Typ | Max | Unit |
|-----------|-----|-----|-----|------|
| Input voltage (VIN) | 4.5 | 5.0 | 28 | V |
| Output voltage (VOUT) | 0.8 | — | 25 | V |
| Output current (IOUT) | 0 | — | 3 | A |
| Operating temperature (TA) | -40 | 25 | 85 | °C |

---

## 4. Electrical Characteristics

**Test Conditions:** VIN = 5V, VOUT = 3.3V, TA = 25°C, unless otherwise noted.

### 4.1 Input Supply

| Parameter | Conditions | Min | Typ | Max | Unit |
|-----------|------------|-----|-----|-----|------|
| VIN operating range | | 4.5 | — | 28 | V |
| VIN undervoltage lockout threshold | Rising | 4.1 | 4.25 | 4.4 | V |
| VIN undervoltage lockout hysteresis | | — | 230 | — | mV |
| Quiescent current | Not switching, VEN = 2V | — | 146 | 200 | µA |
| Shutdown current | VEN = 0V | — | 1.4 | 3 | µA |

### 4.2 Output Voltage

| Parameter | Conditions | Min | Typ | Max | Unit |
|-----------|------------|-----|-----|-----|------|
| Output voltage setpoint | Configured for 3.3V | 3.234 | 3.300 | 3.366 | V |
| Output voltage accuracy | VOUT = 3.3V, IOUT = 0.5A | -1 | — | +1 | % |
| Load regulation | IOUT = 0.1A to 3A | — | 0.2 | 0.5 | % |
| Line regulation | VIN = 4.5V to 28V | — | 0.1 | 0.25 | % |
| Feedback voltage (VSENSE) | | 0.792 | 0.800 | 0.808 | V |

### 4.3 Output Ripple and Transient

| Parameter | Conditions | Min | Typ | Max | Unit |
|-----------|------------|-----|-----|-----|------|
| Output ripple voltage | IOUT = 1A, COUT = 47µF | — | 15 | 30 | mVpp |
| Output ripple voltage | IOUT = 3A, COUT = 47µF | — | 25 | 50 | mVpp |
| Load transient response | IOUT step 1A to 2A | — | 3 | — | % |
| Recovery time | 50% load step | — | 50 | 100 | µs |

### 4.4 Efficiency

| Parameter | Conditions | Min | Typ | Max | Unit |
|-----------|------------|-----|-----|-----|------|
| Efficiency | VIN = 5V, IOUT = 0.1A | 80 | 85 | — | % |
| Efficiency | VIN = 5V, IOUT = 0.5A | 88 | 91 | — | % |
| Efficiency | VIN = 5V, IOUT = 1A | 90 | 93 | — | % |
| Efficiency | VIN = 5V, IOUT = 2A | 91 | 94 | — | % |
| Efficiency | VIN = 5V, IOUT = 3A | 89 | 92 | — | % |
| Efficiency | VIN = 12V, IOUT = 1A | 85 | 88 | — | % |

### 4.5 Switching Characteristics

| Parameter | Conditions | Min | Typ | Max | Unit |
|-----------|------------|-----|-----|-----|------|
| Switching frequency | RT = 100kΩ | 475 | 500 | 525 | kHz |
| Soft start time | CSS = 47nF | — | 4 | — | ms |
| Minimum on-time | | — | 100 | — | ns |
| Minimum off-time | | — | 150 | — | ns |

### 4.6 Protection Features

| Parameter | Conditions | Min | Typ | Max | Unit |
|-----------|------------|-----|-----|-----|------|
| Current limit threshold | | 3.5 | 4.0 | 4.5 | A |
| Thermal shutdown threshold | | — | 165 | — | °C |
| Thermal shutdown hysteresis | | — | 15 | — | °C |
| Power good threshold (rising) | % of VOUT | 90 | 93 | 96 | % |
| Power good threshold (falling) | % of VOUT | 85 | 88 | 91 | % |

---

## 5. Application Circuit

```
        VIN (5V)
           │
           ├─── C1 (10µF) ─── GND
           │
    ┌──────┴──────┐
    │   TPS54302  │
    │             │
    │  VIN    SW ─┼──── L1 (4.7µH) ──┬──── VOUT (3.3V)
    │             │                   │
    │  GND   BOOT │                  C2 (47µF)
    │             │                   │
    └─────────────┘                  GND
```

**Component Values:**
- L1: 4.7µH, 4A saturation
- C1 (input): 10µF ceramic, X5R
- C2 (output): 47µF ceramic, X5R
- R1 (feedback divider): 31.6kΩ
- R2 (feedback divider): 10kΩ

---

## 6. Test Points

| Test Point | Net Name | Purpose |
|------------|----------|---------|
| TP1 | VIN | Input voltage monitoring |
| TP2 | VOUT | Output voltage monitoring |
| TP3 | SW | Switching node for scope |
| TP4 | GND | Ground reference |
| TP5 | IOUT | Output current sense (via shunt) |

---

## 7. Typical Application Tests

### 7.1 Output Voltage Accuracy Test
1. Apply VIN = 5.0V ± 0.1V
2. Set load current IOUT = 0.5A
3. Measure VOUT
4. **Pass criteria:** 3.234V ≤ VOUT ≤ 3.366V

### 7.2 Load Regulation Test
1. Set VIN = 5.0V
2. Measure VOUT at IOUT = 0.1A (V1)
3. Measure VOUT at IOUT = 3.0A (V2)
4. Calculate: Load_Reg = (V1 - V2) / VOUT_nom × 100%
5. **Pass criteria:** Load_Reg ≤ 0.5%

### 7.3 Efficiency Test
1. Set VIN = 5.0V, IOUT = 1A
2. Measure PIN = VIN × IIN
3. Measure POUT = VOUT × IOUT
4. Calculate: Efficiency = POUT / PIN × 100%
5. **Pass criteria:** Efficiency ≥ 90%

### 7.4 Output Ripple Test
1. Set VIN = 5.0V, IOUT = 3A
2. AC couple scope to VOUT (20MHz BW limit)
3. Measure peak-to-peak ripple
4. **Pass criteria:** Ripple ≤ 50mVpp

---

*End of Datasheet Excerpt*
