# Model nhận diện đồ vật (chức năng 7: tìm đồ vật)

| | |
|---|---|
| File | `dfine_s_coco.onnx` (41,8 MB) |
| Model | D-FINE-S (HGNetV2-S), học trên COCO 2017, 80 loại đồ vật, AP 48,5 |
| Nguồn | trọng số chính thức `dfine_s_coco.pth` tại https://github.com/Peterande/D-FINE (release dfinev1.0) |
| Giấy phép | Apache-2.0 (D-FINE) |
| SHA-256 | `6d6ac05cb74bf1c7164ae4d72d6cb8a669ab5d2d5a9c328f49e8d713cc139095` |
| Đầu vào | `images` [N,3,640,640] RGB 0..1, `orig_target_sizes` [N,2] (w,h) |
| Đầu ra | `labels` (0..79, thứ tự COCO-80), `boxes` (xyxy theo pixel ảnh gốc), `scores` |

App tự dùng file này (`shared/vision/detect.default_model_path`). Muốn dùng model khác thì đặt
`AI_CORE_DETECTOR_MODEL=<đường dẫn .onnx>`; đặt `AI_CORE_DETECTOR_MODEL=off` để tắt.

Tìm được: điện thoại (cell phone), điều khiển (remote), cốc (cup), chai (bottle), sách (book), kéo
(scissors), đồng hồ (clock), túi xách (handbag), ba lô (backpack)… **Kính mắt không có trong COCO**, nên
"kính của tôi đâu" cần một model được huấn luyện thêm.

Cách tạo lại: export bằng `tools/deployment/export_onnx.py` của D-FINE (batch 1, opset 16).
