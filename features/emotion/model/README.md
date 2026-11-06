# Real-Time Facial Expression Recognition Using Facial Landmarks

This project is a lightweight real-time facial-expression classification prototype based on normalized MediaPipe facial landmarks and a PyTorch neural network. It detects a face from a webcam feed, extracts selected facial landmarks, normalizes them into a facial feature vector, classifies the visible facial expression, and displays the predicted expression class with confidence on the video frame.

The system classifies visible facial expression labels only. It should not be interpreted as describing intent, mood, or mental state.

## Research Question

How effectively can a lightweight neural network classify facial expressions using normalized MediaPipe facial-landmark geometry rather than raw images?

## Architecture

Webcam -> MediaPipe FaceMesh -> selected facial landmarks -> normalization -> ExpressionNet -> expression classification -> real-time overlay.

The initial expression classes are configurable in [src/config.py](src/config.py):

- Neutral
- Happy
- Sad
- Angry
- Surprised

## Feature Representation

The first working version uses selected MediaPipe FaceMesh coordinates only. It does not include engineered geometric features yet.

The selected 21 landmarks cover central face anchors, eyebrows, eyes, mouth corners, lips, and nearby stabilizing points. Each landmark contributes x, y, and z coordinates, producing a 63-value facial feature vector.

Normalization subtracts a central nose landmark and scales coordinates by the distance between the outer eye landmarks. This reduces sensitivity to face position and distance from the camera.

## Why Facial Landmarks?

Facial landmarks provide a low-dimensional representation of facial geometry. They are efficient, interpretable, and suitable for real-time CPU-friendly inference.

The tradeoff is that landmark-only features discard raw image texture, lighting, skin appearance, and other visual cues that image-based models could use.

## Dataset

The production dataset is expected at:

```bash
data/expression_landmarks.csv
```

Rows are collected with explicit expression labels, feature headers, timestamps, subject IDs, and session IDs.

Session-aware splitting is the default methodology. Training stops if there are not enough complete recording sessions to make train, validation, and held-out test partitions by session.

If the dataset contains only one subject, measured performance should be described as performance for a personalized facial-expression prototype, not evidence of generalization to arbitrary people.

## Model

`ExpressionNet` is a compact multilayer perceptron:

```text
63 input features -> 128 -> 64 -> 5 expression classes
```

It uses ReLU activations and dropout. The model is intentionally small because the input representation is already low-dimensional.

## Baseline

The baseline is logistic regression using the exact same normalized facial-landmark features and the exact same session-aware split as ExpressionNet.

This comparison asks whether the neural network improves over a simpler classifier for this feature representation.

## Evaluation

Evaluation is performed only on held-out test sessions. The evaluator reports:

- accuracy
- macro precision
- macro recall
- macro F1
- per-class precision, recall, and F1
- confusion matrix

Outputs are saved under `results/`.

## Results

Evaluation results are pending data collection. No trained production model or valid held-out evaluation results currently exist in this repository.

After data collection, training, baseline fitting, and held-out evaluation, measured results may be added from `results/metrics.json`. Do not add estimated or invented numbers.

## Install

```bash
python -m venv venv
venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

On macOS/Linux:

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Collect Data

```bash
python -m src.collect_data --subject-id subject_001
```

Use keys `1-5` to select an expression class, `R` to record or pause, and `Q` to quit.

Recommended protocol:

- Collect at least five separate sessions.
- In every session, collect all five expression classes.
- Use meaningful breaks between sessions to reduce near-duplicate leakage.
- Aim for balanced counts per class within each session.
- Prefer multiple subjects if the project will discuss generalization.
- If only one subject is collected, describe the model as personalized.

## Train

```bash
python -m src.train
```

Training creates or reuses a session-aware split file at `data/splits.json`. It saves the best validation checkpoint to `models/expression_net.pth` and writes training history to `results/training_history.csv` and `results/training_history.png`.

## Baseline

```bash
python -m src.baseline
```

## Evaluate

```bash
python -m src.evaluate
```

## Run Webcam Demo

```bash
python -m main
```

The webcam demo displays:

```text
Expression: Happy
Confidence: 91.4%
```

If no face is detected, the app displays `No face detected`. If confidence is below the configured threshold, the app displays `Expression: Uncertain`.

## Tests

```bash
python -m pytest
```

## Limitations

- The project requires newly collected expression labels before training claims are valid.
- A small or single-subject dataset may not generalize to other people.
- Consecutive webcam frames are highly correlated, so session-aware splits are required.
- Exaggerated expressions may be easier to classify than natural expressions.
- Facial landmarks discard raw image texture and appearance cues.
- Facial expression labels describe visible appearance, not intent, mood, or mental state.

## Future Work

- Collect data from more participants.
- Evaluate cross-subject generalization.
- Add interpretable geometric features as a separate experiment.
- Compare against image-based CNN or ViT approaches.
- Explore temporal models over landmark sequences.
- Compare MediaPipe blendshape features with landmark coordinates.
