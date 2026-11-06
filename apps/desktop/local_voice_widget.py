"""Local microphone controls, transcript review, and explicit confirmation."""
from PyQt5.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QLabel, QMessageBox, QFrame, QScrollArea
)


class LocalVoiceWidget(QWidget):
    def __init__(self, controller=None, dispatcher=None, parent=None, activate=None):
        super().__init__(parent)
        self.controller, self.dispatcher = controller, dispatcher
        self.activate = activate
        self.request_coordinator = None
        self._routing = False
        self.last_transcript = ""

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(10, 3, 10, 3)
        root_layout.setSpacing(3)

        # 1. Hàng điều khiển chính: Gom nút thu âm và trạng thái thành 1 hàng gọn gàng
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        root_layout.addLayout(layout)

        self.micro = QPushButton('🎙 NHẬN GIỌNG NÓI')
        self.micro.setObjectName('LocalVoiceMicrophone')
        self.micro.setAccessibleName('Nhận giọng nói')
        self.micro.setStyleSheet('QPushButton { background: #285C4D; color: white; font-size: 12.5px; font-weight: 700; padding: 4px 12px; border-radius: 6px; min-height: 30px; } QPushButton:disabled { background: #E4EBDD; color: #5A6E85; }')

        self.stop = QPushButton('⏹ Dừng')
        self.stop.setStyleSheet('QPushButton { background: #FFFFFF; color: #183A34; border: 1px solid #CBD5E1; font-size: 12px; font-weight: 600; padding: 4px 10px; border-radius: 6px; min-height: 30px; } QPushButton:hover { background: #F1F5F9; }')

        self.cancel = QPushButton('✖ Hủy')
        self.cancel.setStyleSheet('QPushButton { background: #FFFFFF; color: #183A34; border: 1px solid #CBD5E1; font-size: 12px; font-weight: 600; padding: 4px 10px; border-radius: 6px; min-height: 30px; } QPushButton:hover { background: #F1F5F9; }')

        self.confirm_button = QPushButton('✔ Xác nhận')
        self.confirm_button.setEnabled(False)
        self.confirm_button.setStyleSheet('QPushButton { background: #E4EBDD; color: #5A6E85; font-size: 12px; font-weight: 700; padding: 4px 12px; border-radius: 6px; min-height: 30px; }')

        for button in (self.micro, self.stop, self.cancel, self.confirm_button):
            layout.addWidget(button)

        self.status = QLabel('Đang chuẩn bị nhận giọng nói local...')
        self.status.setWordWrap(True)
        self.status.setStyleSheet('color: #667B74; font-size: 12px; padding: 0 4px;')
        layout.addWidget(self.status, 1)

        self.verification_badge = QLabel('Chờ nhận diện')
        self.verification_badge.setStyleSheet('background: #F1F5F9; color: #70817D; font-size: 11px; font-weight: 700; padding: 3px 8px; border-radius: 4px;')
        layout.addWidget(self.verification_badge)

        self.btn_toggle_details = QPushButton('🔽 Chi tiết')
        self.btn_toggle_details.setStyleSheet('QPushButton { background: #F8FAF7; border: 1px solid #CBD5E1; border-radius: 5px; padding: 3px 8px; font-size: 11.5px; font-weight: 600; color: #475569; min-height: 28px; } QPushButton:hover { background: #EDF2F7; }')
        self.btn_toggle_details.clicked.connect(self._toggle_details)
        layout.addWidget(self.btn_toggle_details)

        # 2. KHUNG CHI TIẾT (Thu gọn / mở rộng, không chiếm chiều cao camera)
        self.details_container = QFrame(self)
        self.details_container.setObjectName('VoiceDetailsContainer')
        self.details_container.setVisible(False)
        lay_details = QVBoxLayout(self.details_container)
        lay_details.setContentsMargins(0, 2, 0, 0)
        lay_details.setSpacing(3)

        # Hàng nút tắt nhanh gửi yêu cầu
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(6)
        quick_btn_style = (
            'QPushButton { background: #FFFFFF; border: 1px solid #CBD5E1; border-radius: 5px; '
            'padding: 3px 8px; font-size: 11.5px; font-weight: 600; color: #334155; min-height: 26px; } '
            'QPushButton:hover { background: #F1F5F9; border-color: #285C4D; color: #285C4D; }'
        )
        self.doctor_button = QPushButton('👨‍⚕️ Bác sĩ')
        self.caregiver_button = QPushButton('👪 Người nhà')
        self.false_alarm = QPushButton('⚠️ Báo nhầm')
        self.resend_button = QPushButton('🔄 Gửi lại')
        self.retry_button = QPushButton('🔁 Thử lại')
        self.manual_button = QPushButton('📞 Liên hệ thủ công')

        for button in (self.doctor_button, self.caregiver_button, self.false_alarm, self.resend_button, self.retry_button, self.manual_button):
            button.setStyleSheet(quick_btn_style)
            actions_layout.addWidget(button)
        lay_details.addLayout(actions_layout)

        # Transcript Box với chiều cao giới hạn và thanh cuộn khi dài
        self.transcript_card = QFrame(self)
        self.transcript_card.setObjectName('VoiceTranscriptCard')
        self.transcript_card.setStyleSheet('''
            QFrame#VoiceTranscriptCard {
                background: #FFFFFF;
                border: 1.5px solid #DFE5DF;
                border-radius: 6px;
            }
        ''')
        card_layout = QVBoxLayout(self.transcript_card)
        card_layout.setContentsMargins(8, 4, 8, 4)
        card_layout.setSpacing(2)

        header_layout = QHBoxLayout()
        self.transcript_title = QLabel('🎙 VĂN BẢN NHẬN DIỆN TỪ GIỌNG NÓI:')
        self.transcript_title.setStyleSheet('color: #285C4D; font-size: 11px; font-weight: 700;')
        header_layout.addWidget(self.transcript_title)
        header_layout.addStretch()
        card_layout.addLayout(header_layout)

        self.transcript_scroll = QScrollArea()
        self.transcript_scroll.setWidgetResizable(True)
        self.transcript_scroll.setMaximumHeight(50)
        self.transcript_scroll.setFrameShape(QFrame.NoFrame)
        self.transcript_scroll.setStyleSheet('background: transparent; border: none;')

        self.transcript_label = QLabel('(Chưa có lời nói nào được nhận diện. Bác hãy nhấn "🎙 NHẬN GIỌNG NÓI" để bắt đầu...)')
        self.transcript_label.setObjectName('VoiceTranscriptDisplay')
        self.transcript_label.setWordWrap(True)
        self.transcript_label.setStyleSheet('''
            QLabel#VoiceTranscriptDisplay {
                background: #F8FAF7;
                color: #667B74;
                font-size: 12px;
                font-weight: 600;
                font-style: italic;
                padding: 3px 8px;
                border-radius: 4px;
                border: 1px dashed #CBD5E1;
            }
        ''')
        self.transcript_scroll.setWidget(self.transcript_label)
        card_layout.addWidget(self.transcript_scroll)

        self.prompt_label = QLabel('👉 Mẹo: Bác có thể nói "Gọi bác sĩ", "Gọi con", "Báo bác sĩ và người nhà", "Tôi đã uống thuốc rồi" hoặc "Cứu tôi với".')
        self.prompt_label.setWordWrap(True)
        self.prompt_label.setStyleSheet('color: #70817D; font-size: 10.5px; font-weight: 500;')
        card_layout.addWidget(self.prompt_label)

        lay_details.addWidget(self.transcript_card)
        root_layout.addWidget(self.details_container)

        # Gán sự kiện
        self.micro.clicked.connect(self.start_recording)
        self.stop.clicked.connect(lambda: self.controller.stop() if self.controller else None)
        self.cancel.clicked.connect(self.cancel_request)
        self.confirm_button.clicked.connect(lambda: self.request_coordinator.confirm() if hasattr(self.request_coordinator, 'confirm') else None)
        self.doctor_button.clicked.connect(lambda: self.select_request('Gọi bác sĩ giúp tôi'))
        self.caregiver_button.clicked.connect(lambda: self.select_request('Gọi con giúp tôi'))
        self.false_alarm.clicked.connect(lambda: self.select_request('Báo nhầm'))
        self.resend_button.clicked.connect(self.resend)
        self.manual_button.clicked.connect(lambda: self.request_coordinator.manual_contacts() if hasattr(self.request_coordinator, 'manual_contacts') else None)
        self.retry_button.clicked.connect(lambda: self.request_coordinator.retry_delivery() if hasattr(self.request_coordinator, 'retry_delivery') else None)

        if controller:
            self.attach(controller, dispatcher)
        else:
            self.update_state('off', 'Nhấn Nhận giọng nói để chuẩn bị. Khi sẵn sàng, nhấn để nói; Dừng để xử lý, Hủy để bỏ.')

    def _toggle_details(self):
        visible = not self.details_container.isVisible()
        self.details_container.setVisible(visible)
        self.btn_toggle_details.setText('🔼 Thu gọn' if visible else '🔽 Chi tiết')

    def _set_transcript(self, text):
        self.last_transcript = text or ""
        if text:
            self.transcript_label.setText(f'“{text}”')
            self.transcript_label.setStyleSheet('''
                QLabel#VoiceTranscriptDisplay {
                    background: #EAF0E2;
                    color: #183A34;
                    font-size: 14px;
                    font-weight: 700;
                    padding: 4px 10px;
                    border-radius: 6px;
                    border: 1.5px solid #285C4D;
                }
            ''')
        else:
            self.transcript_label.setText('(Chưa có lời nói nào được nhận diện. Bác hãy nhấn "🎙 NHẬN GIỌNG NÓI" để bắt đầu...)')
            self.transcript_label.setStyleSheet('''
                QLabel#VoiceTranscriptDisplay {
                    background: #F8FAF7;
                    color: #667B74;
                    font-size: 13px;
                    font-weight: 600;
                    font-style: italic;
                    padding: 4px 10px;
                    border-radius: 6px;
                    border: 1px dashed #CBD5E1;
                }
            ''')

    def attach(self, controller, dispatcher):
        self.controller, self.dispatcher = controller, dispatcher
        controller.changed.connect(self.update_state)
        captured_signal = getattr(controller, 'transcript_captured', controller.transcript_ready)
        captured_signal.connect(self._on_transcript_ready)
        import os
        backend = os.getenv('VOICE_SEMANTIC_BACKEND', 'local_rules')
        if backend == 'local_rules':
            from features.voice_assistant.local_rules.desktop import LocalRulesCoordinator
            self.request_coordinator = LocalRulesCoordinator(dispatcher, self)
        elif backend == 'gemini':
            from features.voice_assistant.transcript_intent.coordinator import VoiceRequestCoordinator
            self.request_coordinator = VoiceRequestCoordinator(dispatcher.snapshot, self)
        else:
            raise ValueError('VOICE_SEMANTIC_BACKEND must be local_rules or gemini')
        self.request_coordinator.changed.connect(self.routing_state)
        self.request_coordinator.resolved.connect(self.review)
        controller.transcript_ready.connect(self.request_coordinator.route)
        self.update_state(controller.state, 'Đang chuẩn bị nhận giọng nói...')

    def _on_transcript_ready(self, payload, context):
        if isinstance(payload, dict) and payload.get('transcript'):
            self._set_transcript(payload['transcript'])

    def select_request(self, text, allow_repeat=False):
        if not self.request_coordinator or not hasattr(self.request_coordinator, 'confirm'):
            return
        self._set_transcript(text)
        import uuid
        captured = self.dispatcher.snapshot()
        self.request_coordinator.route(dict(transcript=text, session_id=uuid.uuid4().hex,
                                            is_final=True, status='completed', allow_repeat=allow_repeat), captured)

    def resend(self):
        text = getattr(self.request_coordinator, 'last_transcript', '') or self.last_transcript
        if text:
            self.select_request(text, allow_repeat=True)

    def cancel_request(self):
        if self.request_coordinator:
            self.request_coordinator.cancel()
        if self.controller:
            self.controller.cancel()
        self.verification_badge.setText('✖ ĐÃ HỦY')
        self.verification_badge.setStyleSheet('background: #F1F5F9; color: #70817D; font-size: 11px; font-weight: 700; padding: 2px 6px; border-radius: 4px;')
        self.prompt_label.setText('👉 Yêu cầu đã được hủy bỏ do nhận diện sai hoặc người dùng hủy. Bác có thể nói lại câu lệnh mới.')
        self.prompt_label.setStyleSheet('color: #667B74; font-size: 11px; font-weight: 500;')
        self.transcript_card.setStyleSheet('QFrame#VoiceTranscriptCard { background: #FFFFFF; border: 1.5px solid #DFE5DF; border-radius: 8px; }')
        self.confirm_button.setText('✔ Xác nhận')
        self.confirm_button.setStyleSheet('QPushButton { background: #E4EBDD; color: #5A6E85; font-size: 12px; font-weight: 700; padding: 5px 12px; border-radius: 6px; }')
        self.cancel.setText('Hủy')
        self.cancel.setStyleSheet('')

    def routing_state(self, state, message):
        if state == 'delivery_status':
            if self.controller and self.controller.state in ('idle', 'unavailable') and not self._routing:
                self._review_text = message
                self.status.setText(message)
            return
        if state == 'routing':
            self._review_text = None
            self.verification_badge.setText('⏳ ĐANG XỬ LÝ...')
            self.verification_badge.setStyleSheet('background: #EAF0E2; color: #1D4ED8; font-size: 11px; font-weight: 700; padding: 2px 6px; border-radius: 4px;')
            self.prompt_label.setText('⏳ Đang xử lý yêu cầu...')
            self.prompt_label.setStyleSheet('color: #1D4ED8; font-size: 11px; font-weight: 500;')

        self.confirm_button.setEnabled(state == 'pending')
        self.cancel.setEnabled(state == 'pending')
        self._routing = state not in ('routing_finished', 'pending')

        if state == 'pending':
            self.details_container.setVisible(True)
            self.btn_toggle_details.setText('🔼 Thu gọn')
            self._review_text = message
            self.update_state(self.controller.state if self.controller else 'idle', message)

            # Trích xuất lời nói nếu chưa gán
            if not self.last_transcript and message.startswith('“') and '”' in message:
                extracted = message[1:message.index('”')]
                self._set_transcript(extracted)

            # Cập nhật khung hiển thị nổi bật để người dùng kiểm tra đúng/sai
            self.transcript_card.setStyleSheet('QFrame#VoiceTranscriptCard { background: #FFFFFF; border: 1.5px solid #285C4D; border-radius: 8px; }')
            self.verification_badge.setText('⚠️ VUI LÒNG KIỂM TRA & XÁC NHẬN')
            self.verification_badge.setStyleSheet('background: #FEF3C7; color: #B45309; font-size: 11px; font-weight: 700; padding: 2px 6px; border-radius: 4px; border: 1px solid #FDE68A;')
            self.prompt_label.setText('👉 Bác vui lòng kiểm tra xem hệ thống nhận diện ĐÚNG chưa:\n• Nếu ĐÚNG: Nhấn [✔ Xác nhận] (hoặc nói "Đồng ý").\n• Nếu SAI: Nhấn [✖ Hủy] (hoặc nói "Hủy").')
            self.prompt_label.setStyleSheet('background: #FEF9C3; color: #854D0E; font-size: 12px; font-weight: 600; padding: 4px 8px; border-radius: 6px; border: 1px solid #FDE047;')

            # Đổi nút Xác nhận sang màu xanh lá nổi bật, nút Hủy sang màu đỏ cảnh báo
            self.confirm_button.setText('✔ Xác nhận (Đúng lời nói)')
            self.confirm_button.setStyleSheet('QPushButton { background: #167D62; color: white; font-size: 12px; font-weight: 700; padding: 5px 12px; border-radius: 6px; border: 1.5px solid #0D5C46; } QPushButton:hover { background: #126650; }')
            self.cancel.setText('✖ Hủy (Nhận diện sai)')
            self.cancel.setStyleSheet('QPushButton { background: #FEECEE; color: #C3293E; font-size: 12px; font-weight: 700; padding: 5px 12px; border-radius: 6px; border: 1.5px solid #F8C1C8; } QPushButton:hover { background: #FCD8DC; }')
            return

        if state == 'routing_finished':
            self.verification_badge.setText('✔ ĐÃ XỬ LÝ')
            self.verification_badge.setStyleSheet('background: #D1FAE5; color: #047857; font-size: 11px; font-weight: 700; padding: 2px 6px; border-radius: 4px; border: 1px solid #A7F3D0;')
            self.prompt_label.setText('✔ Kết quả: ' + message)
            self.prompt_label.setStyleSheet('color: #183A34; font-size: 11px; font-weight: 500;')
            self.transcript_card.setStyleSheet('QFrame#VoiceTranscriptCard { background: #FFFFFF; border: 1.5px solid #DFE5DF; border-radius: 8px; }')
            self.confirm_button.setText('✔ Xác nhận')
            self.confirm_button.setStyleSheet('QPushButton { background: #E4EBDD; color: #5A6E85; font-size: 12px; font-weight: 700; padding: 5px 12px; border-radius: 6px; }')
            self.cancel.setText('Hủy')
            self.cancel.setStyleSheet('')

        self._review_text = message
        if self._routing:
            self.micro.setEnabled(False)
            self.stop.setEnabled(False)
            self.cancel.setEnabled(state == 'routing')
            self.status.setText(message)
        else:
            self.update_state(self.controller.state if self.controller else 'idle', getattr(self, '_review_text', None) or message)

    def clear_review(self):
        if self.controller and self.controller.state in ('starting', 'recording', 'stopping', 'transcribing'):
            return
        self._review_text = None
        self._set_transcript('')
        self.verification_badge.setText('Chờ nhận diện')
        self.verification_badge.setStyleSheet('background: #F1F5F9; color: #70817D; font-size: 11px; font-weight: 700; padding: 2px 6px; border-radius: 4px;')
        self.prompt_label.setText('👉 Mẹo: Nói "Gọi bác sĩ", "Gọi con", "Báo bác sĩ và người nhà", "Tôi đã uống thuốc rồi" hoặc "Cứu tôi với".')
        self.prompt_label.setStyleSheet('color: #667B74; font-size: 11px; font-weight: 500;')
        self.transcript_card.setStyleSheet('QFrame#VoiceTranscriptCard { background: #FFFFFF; border: 1.5px solid #DFE5DF; border-radius: 8px; }')
        if self.controller:
            self.controller.review_message = ''
        if not self._routing:
            if self.controller and self.controller.state in ('starting', 'recording', 'stopping', 'transcribing'):
                return
            self.status.setText('Ngữ cảnh đã đổi. Bác có thể nói lại hoặc chọn nút trên màn hình.')

    def shutdown(self):
        if self.request_coordinator:
            self.request_coordinator.shutdown()

    def start_recording(self):
        if self.request_coordinator and self.request_coordinator.busy and getattr(self.request_coordinator, 'recording_blocked', True):
            return
        if self.controller is None:
            if self.activate:
                self.activate()
                if self.controller:
                    self.controller.start()
            return
        if self.controller.state == 'unavailable':
            self.controller.retry()
            self.controller.start()
            return
        music = getattr(self.dispatcher.window.cognitive_games_widget, 'music_widget', None)
        if music and music.audio_service.is_playing and not music.audio_service.is_paused:
            music._on_pause_clicked()
        self.controller.start()

    def update_state(self, state, message):
        if state in ('loading', 'starting', 'recording'):
            self._review_text = None
        if state == 'recording':
            self.verification_badge.setText('🎙 ĐANG LẮNG NGHE...')
            self.verification_badge.setStyleSheet('background: #FEE2E2; color: #DC2626; font-size: 11px; font-weight: 700; padding: 2px 6px; border-radius: 4px; border: 1px solid #FCA5A5;')
            self.transcript_label.setText('Đang lắng nghe giọng nói... Bác hãy nói câu lệnh.')
            self.transcript_label.setStyleSheet('''
                QLabel#VoiceTranscriptDisplay {
                    background: #FEF2F2;
                    color: #991B1B;
                    font-size: 13px;
                    font-weight: 600;
                    padding: 4px 10px;
                    border-radius: 6px;
                    border: 1px solid #F87171;
                }
            ''')
            self.prompt_label.setText('👉 Nói xong câu lệnh, bác nhấn nút "Dừng" để hệ thống chuyển thành chữ.')
        elif state == 'transcribing':
            self.verification_badge.setText('🔄 ĐANG CHUYỂN THÀNH CHỮ...')
            self.verification_badge.setStyleSheet('background: #EEF2FF; color: #4338CA; font-size: 11px; font-weight: 700; padding: 2px 6px; border-radius: 4px; border: 1px solid #C7D2FE;')
            self.transcript_label.setText('Đang chuyển đổi giọng nói thành văn bản...')
            self.prompt_label.setText('Vui lòng đợi trong giây lát...')
        elif state in ('idle', 'off') and not getattr(getattr(self.request_coordinator, 'dialogue', None), 'pending', None) and not self._routing:
            if not self.last_transcript:
                self.verification_badge.setText('Sẵn sàng')
                self.verification_badge.setStyleSheet('background: #F1F5F9; color: #70817D; font-size: 12px; font-weight: 700; padding: 3px 8px; border-radius: 4px;')

        if self._routing:
            return
        self.micro.setEnabled(state in ('idle', 'unavailable', 'off'))
        self.micro.setText('🎙 Thử lại nhận giọng nói' if state == 'unavailable' else '🎙 NHẬN GIỌNG NÓI')
        self.stop.setEnabled(state == 'recording')
        pending = getattr(getattr(self.request_coordinator, 'dialogue', None), 'pending', None)
        self.confirm_button.setEnabled(bool(pending))
        self.cancel.setEnabled(bool(pending) or state in ('starting', 'recording', 'stopping', 'transcribing'))
        if state in ('idle', 'review') and getattr(self, '_review_text', None):
            message = self._review_text
        elif state == 'unavailable' and pending:
            preview = getattr(self.request_coordinator, 'pending_message', '')
            if preview and message != preview:
                message = preview + '\n' + message
        self.status.setText(message)

    def review(self, result, captured, proposed=None):
        def confirm(text):
            return QMessageBox.question(self, 'Xác nhận lần uống thuốc', text,
                                         QMessageBox.Yes | QMessageBox.No,
                                         QMessageBox.No) == QMessageBox.Yes
        try:
            message = self.dispatcher.dispatch(result, captured, confirm, proposed=proposed)
        except Exception:
            message = 'Chưa thực hiện được lệnh. Hãy dùng nút bấm và kiểm tra đồng bộ.'
        # Keep the transcript visible after controller returns to idle.
        transcript_text = result.get('transcript', '') if isinstance(result, dict) else str(result)
        self._set_transcript(transcript_text)
        self._review_text = f'“{transcript_text}” — {message}'
        if self.controller:
            self.controller.review_message = self._review_text
        self.status.setText(self._review_text)
