from src.config import CLASS_TO_INDEX, EXPRESSION_CLASSES, FEATURE_COLUMNS, INPUT_SIZE, SELECTED_FACE_LANDMARKS


def test_class_mapping_is_stable():
    assert EXPRESSION_CLASSES == ["Neutral", "Happy", "Sad", "Angry", "Surprised"]
    assert CLASS_TO_INDEX["Neutral"] == 0
    assert CLASS_TO_INDEX["Surprised"] == 4


def test_feature_columns_match_selected_landmarks():
    assert len(SELECTED_FACE_LANDMARKS) == 21
    assert INPUT_SIZE == 63
    assert len(FEATURE_COLUMNS) == INPUT_SIZE
    assert FEATURE_COLUMNS[0] == "landmark_1_x"
