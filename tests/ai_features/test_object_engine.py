from datetime import datetime
from zoneinfo import ZoneInfo

from ai_core.object_finder.domain import (
    DetectionFrame,
    DetectionIn,
    ObjectConfig,
    Region,
    RegisteredItem,
    SightingStatus,
    normalize_vi,
)
from ai_core.object_finder.engine import ObjectMemory

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
T0 = datetime(2026, 11, 3, 14, 0, tzinfo=TZ).timestamp()

TABLE = Region(region_id="ban-nuoc", name="bàn uống nước", polygon=[(0.0, 0.5), (0.5, 0.5), (0.5, 1.0), (0.0, 1.0)])
SHELF = Region(region_id="tu-dau-giuong", name="tủ đầu giường", polygon=[(0.6, 0.0), (1.0, 0.0), (1.0, 0.4), (0.6, 0.4)])
ON_TABLE = (0.2, 0.7, 0.06, 0.04)
ON_SHELF = (0.8, 0.2, 0.06, 0.04)

RED = (1.0, 0.0, 0.0)
BLACK = (0.0, 1.0, 0.0)


def mem(**kw):
    return ObjectMemory("P1", config=ObjectConfig(**kw), regions=[TABLE, SHELF])


def det(label, box, score=0.8, emb=None):
    return DetectionIn(label, score, box, emb, "test" if emb else None)


def see(m, t, *dets, persons=(), scanned=None):
    return m.ingest(DetectionFrame(t, 0, tuple(dets), tuple(persons), scanned))


def show(m, t, label, box, n=3, step=1.0, **kw):
    events = []
    for i in range(n):
        events += see(m, t + i * step, det(label, box, **kw))
    return events, t + n * step


def test_normalize():
    assert normalize_vi("Kính của tôi ĐÂU rồi?") == "kinh cua toi dau roi"
    assert normalize_vi("Điều khiển") == "dieu khien"


def test_single_detection_is_not_a_sighting():
    m = mem()
    assert see(m, T0, det("remote", ON_TABLE)) == []
    assert m.answer("điều khiển đâu", T0 + 5).status == "not_seen"


def test_confirmed_after_stable_hits_and_answer_fresh():
    m = mem()
    events, t = show(m, T0, "remote", ON_TABLE)
    assert [e.change for e in events] == ["confirmed"]
    s = events[0].sighting
    assert s.region_id == "ban-nuoc" and s.type_key == "remote"
    a = m.answer("Cái điều khiển ti vi đâu rồi?", t + 60)
    assert a.status == "found"
    assert "bàn uống nước" in a.text and "vừa thấy" in a.text


def test_answer_hours_later_uses_clock_time():
    m = mem()
    show(m, T0, "cell phone", ON_SHELF)
    a = m.answer("điện thoại của tôi đâu", T0 + 2 * 3600)
    assert a.status == "found"
    assert "tủ đầu giường" in a.text and "lúc 14:00" in a.text and "cách đây 2 tiếng" in a.text


def test_stale_answer_suggests_usual_places():
    m = mem()
    show(m, T0, "remote", ON_SHELF)
    show(m, T0 + 100, "remote", ON_TABLE)
    show(m, T0 + 200, "remote", ON_TABLE)
    # Last seen on the shelf long ago
    show(m, T0 + 300, "remote", ON_SHELF)
    a = m.answer("điều khiển", T0 + 10 * 3600)
    assert a.status == "found"
    assert "Đã lâu rồi" in a.text and "bàn uống nước" in a.suggestions


def test_gone_when_missing_without_hand():
    m = mem()
    _, t = show(m, T0, "remote", ON_TABLE)
    events = []
    for i in range(3):
        events += see(m, t + i)
    assert [e.change for e in events] == ["status"]
    assert events[0].sighting.status == SightingStatus.GONE
    a = m.answer("điều khiển đâu", t + 600)
    assert a.status == "gone" and "không còn ở đó" in a.text


def test_holding_when_a_wrist_was_near():
    m = mem()
    _, t = show(m, T0, "cup", ON_TABLE)
    cx, cy = ON_TABLE[0] + 0.03, ON_TABLE[1] + 0.02
    m.note_wrists(t, 0, [(cx + 0.02, cy)])
    events = []
    for i in range(3):
        events += see(m, t + 1 + i)
    assert events[-1].sighting.status == SightingStatus.HOLDING
    a = m.answer("cái cốc của tôi", t + 30)
    assert a.status == "holding" and "cầm lên" in a.text


def test_occlusion_and_unscanned_region_do_not_count_as_gone():
    m = mem()
    _, t = show(m, T0, "remote", ON_TABLE)
    person = (0.1, 0.4, 0.3, 0.6)  # standing in front of the table
    for i in range(10):
        assert see(m, t + i, persons=[person]) == []
    for i in range(10):  # scans that only covered the shelf
        assert see(m, t + 20 + i, scanned=("tu-dau-giuong",)) == []
    assert m.answer("điều khiển", t + 40).status == "found"


def test_registered_item_identified_by_embedding_and_moves():
    m = mem()
    red = RegisteredItem("a" * 32, "P1", "glasses", "kính lão của bà", owner="bà", embeddings=[list(RED)], embedder_id="test")
    black = RegisteredItem("b" * 32, "P1", "glasses", "kính đen của ông", owner="ông", embeddings=[list(BLACK)], embedder_id="test")
    m.upsert_item(red)
    m.upsert_item(black)
    for i in range(3):
        see(m, T0 + i, det("glasses", ON_TABLE, emb=RED), det("glasses", ON_SHELF, emb=BLACK))
    a = m.answer("kính của bà đâu", T0 + 60)
    assert a.item_key == red.item_id and "bàn uống nước" in a.text
    a = m.answer("kính của ông", T0 + 60)
    assert a.item_key == black.item_id and "tủ đầu giường" in a.text
    # The red pair shows up on the shelf: it moved (the table place is superseded).
    events = []
    for i in range(3):
        events += see(m, T0 + 100 + i, det("glasses", (0.7, 0.1, 0.06, 0.04), emb=RED), det("glasses", ON_SHELF, emb=BLACK))
    changes = {(e.sighting.item_key, e.change, e.sighting.status) for e in events}
    assert (red.item_id, "moved", SightingStatus.SEEN) in changes
    assert (red.item_id, "status", SightingStatus.GONE) in changes
    assert "tủ đầu giường" in m.answer("kính lão của bà", T0 + 200).text


def test_unidentified_object_answer_is_hedged():
    m = mem()
    m.upsert_item(RegisteredItem("a" * 32, "P1", "glasses", "kính lão", embeddings=[list(RED)], embedder_id="test"))
    show(m, T0, "glasses", ON_TABLE)  # no embedding: cannot tell whose glasses
    a = m.answer("kính của tôi đâu", T0 + 60)
    assert a.status == "found" and "chưa chắc" in a.text and a.confidence < 0.8


def test_two_unregistered_cups():
    m = mem()
    for i in range(3):
        see(m, T0 + i, det("cup", ON_TABLE), det("cup", ON_SHELF))
    a = m.answer("cái ly đâu", T0 + 30)
    assert a.status == "found" and len(a.others) == 1 and "một chiếc nữa" in a.text


def test_unknown_item_and_mark_not_there():
    m = mem()
    assert m.answer("hôm nay trời thế nào", T0).status == "unknown_item"
    events, t = show(m, T0, "book", ON_TABLE)
    s = m.mark_not_there(events[0].sighting.sighting_id, t)
    assert s is not None and s.status == SightingStatus.GONE
    assert m.answer("quyển sách", t + 10).status == "gone"


def test_restore_after_restart():
    m = mem()
    events, t = show(m, T0, "remote", ON_TABLE)
    m2 = mem()
    m2.restore([events[0].sighting], {"type:remote": {"ban-nuoc": 4}})
    a = m2.answer("điều khiển", t + 60)
    assert a.status == "found" and "bàn uống nước" in a.text
    # Still tracked: misses after restart mark it gone.
    for i in range(3):
        see(m2, t + 100 + i)
    assert m2.answer("điều khiển", t + 200).status == "gone"
