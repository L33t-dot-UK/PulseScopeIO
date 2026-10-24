# Synthetic regression fixtures

These CSV files contain synthetic encoder signals, not customer or radar recordings. They are retained because the regression suite uses their exact samples.

- `audit/fixtures/`: clean, duty, jitter, missing-pulse, reversed, index-glitch and unlabelled health-check signals.
- `audit/online-*.json`: numerical results from synthetic captures used as regression references. These files contain measurements, not an archived website or third-party application code.
- `examples/`: small synthetic captures, including the three four-channel merge examples.

Additional captures created while running tests belong in `.cache/` and are ignored by Git. Do not add real recordings or personal capture paths here.
