"""Accessible schedule editor and persistent reminder UI; no camera dependency."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QTableWidget, QTableWidgetItem, QMessageBox, QDialog, QFormLayout,
    QLineEdit, QCheckBox, QSpinBox, QDialogButtonBox, QHeaderView, QAbstractItemView)

from features.medication.medication_service import validate_schedule, due_times

STATUS = {'PENDING':'Sắp tới', 'REMINDING':'Đến giờ — chưa có xác nhận',
          'SNOOZED':'Đã hoãn nhắc', 'CONFIRMED':'Người dùng đã xác nhận',
          'SKIPPED':'Người dùng chọn bỏ qua', 'OVERDUE':'Quá hạn — chưa có xác nhận',
          'MISSED':'Đã qua ngày — chưa có xác nhận', 'CANCELLED':'Lịch cũ đã thay đổi'}
EMAIL_STATUS = {'PENDING':'Chưa đến lúc gửi', 'DISABLED':'Không bật gửi', 'SENDING':'Đang gửi',
                'SMTP_ACCEPTED':'SMTP chấp nhận; chưa biết đã nhận/đọc', 'DRY_RUN':'Kiểm thử — không gửi thư',
                'UNCONFIGURED':'Chưa cấu hình Gmail', 'INVALID_EMAIL':'Thiếu email hợp lệ',
                'FAILED_AUTH':'Lỗi xác thực', 'FAILED_NETWORK':'Lỗi kết nối',
                'FAILED_RECIPIENT':'SMTP từ chối người nhận', 'DELIVERY_UNKNOWN':'Không rõ; không tự gửi lại',
                'SUPPRESSED_OLD':'Không gửi dồn sau gián đoạn'}


class ScheduleDialog(QDialog):
    def __init__(self, users, existing=None, parent=None, clock=None):
        super().__init__(parent)
        from features.medication.medication_service import utcnow
        self.clock = clock or utcnow
        self.setWindowTitle('Lịch do người chăm sóc nhập')
        self.setMinimumWidth(640)
        self.setStyleSheet('QLabel, QLineEdit, QComboBox, QSpinBox {font-size:16px;}')
        self.users = users
        self.original = existing or {}
        layout = QVBoxLayout(self)
        info = QLabel('Nhập nguyên văn tên thuốc và hướng dẫn theo đơn. Không tự suy ra liều.\n'
                      'Người chăm sóc chịu trách nhiệm kiểm tra và lưu lịch trên máy này.')
        info.setWordWrap(True)
        layout.addWidget(info)
        form = QFormLayout()
        layout.addLayout(form)
        self.patient = QComboBox()
        for u in users.get_all_users():
            uid = str(u.get('user_id') or u.get('patient_id'))
            self.patient.addItem(f"{u.get('name', uid)} ({uid})", uid)
        self.patient.setCurrentIndex(max(0, self.patient.findData(self.original.get('patient_id'))))
        self.patient.setEnabled(not bool(existing))
        form.addRow('Người dùng', self.patient)
        self.fields = {}
        defaults = {'medication_name':'', 'instructions_text':'', 'local_times':'08:00',
                    'timezone':'Asia/Ho_Chi_Minh', 'start_date':self.clock().astimezone(ZoneInfo('Asia/Ho_Chi_Minh')).date().isoformat(), 'end_date':''}
        labels = ['Tên thuốc', 'Hướng dẫn nguyên văn', 'Giờ (HH:MM, HH:MM)', 'Múi giờ IANA', 'Bắt đầu (YYYY-MM-DD)', 'Kết thúc (để trống nếu không có)']
        for (key, default), label in zip(defaults.items(), labels):
            value = self.original.get(key, default)
            if isinstance(value, list):
                value = ', '.join(value)
            field = QLineEdit(value or '')
            self.fields[key] = field
            form.addRow(label, field)
        days = QHBoxLayout()
        self.days = []
        for i, name in enumerate(['T2','T3','T4','T5','T6','T7','CN']):
            box = QCheckBox(name)
            box.setChecked(i in self.original.get('weekdays', range(7)))
            days.addWidget(box)
            self.days.append(box)
        form.addRow('Lặp theo ngày', days)
        for key, label, default in [('reminder_delay_minutes','Nhắc lại sau (phút)',10), ('overdue_minutes','Quá hạn sau (phút)',60)]:
            box = QSpinBox()
            box.setRange(1,1440)
            box.setValue(self.original.get(key,default))
            self.fields[key] = box
            form.addRow(label,box)
        self.alert = QCheckBox('Cho phép gửi email khi quá hạn chưa xác nhận')
        self.alert.setChecked(self.original.get('caregiver_alert_enabled',False))
        form.addRow(self.alert)
        self.recipient = QLabel()
        self.recipient.setTextFormat(Qt.PlainText)
        form.addRow('Email từ hồ sơ', self.recipient)
        self.patient.currentIndexChanged.connect(self.update_recipient)
        self.update_recipient()
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet('color:#B02030')
        layout.addWidget(self.error)
        preview = QPushButton('Xem trước 7 ngày')
        preview.clicked.connect(self.preview)
        layout.addWidget(preview)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def update_recipient(self):
        email = self.users.get_caregiver_email(self.patient.currentData())
        self.recipient.setText(email or 'Chưa có — đăng ký email trong Hồ sơ người dùng')

    def data(self):
        d = {k:(v.value() if isinstance(v,QSpinBox) else v.text().strip()) for k,v in self.fields.items()}
        d['local_times'] = [t.strip() for t in d['local_times'].split(',')]
        d.update(patient_id=self.patient.currentData(), weekdays=[i for i,b in enumerate(self.days) if b.isChecked()],
                 caregiver_alert_enabled=self.alert.isChecked(), active=True)
        if not d['patient_id']:
            raise ValueError('Cần đăng ký người dùng trước khi tạo lịch')
        if d['caregiver_alert_enabled'] and not self.users.get_caregiver_email(d['patient_id']):
            raise ValueError('Cần email người chăm sóc hợp lệ trong hồ sơ')
        return validate_schedule(d)

    def preview(self):
        try:
            d = self.data()
            first = max(date.fromisoformat(d['start_date']), self.clock().astimezone(ZoneInfo(d['timezone'])).date())
            lines = [local for _,local in due_times(d,first,first+timedelta(days=6))]
            QMessageBox.information(self,'Xem trước 7 ngày', '\n'.join(lines) or 'Không có lần nhắc trong 7 ngày này')
            self.error.clear()
        except (ValueError, KeyError) as e:
            self.error.setText(str(e))

    def save(self):
        try:
            self.result_data = self.data()
            self.accept()
        except (ValueError, KeyError) as e:
            self.error.setText(str(e))


class MedicationPanel(QWidget):
    def __init__(self, service, users, email, parent=None):
        super().__init__(parent)
        self.service, self.users, self.email = service, users, email
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='medication-email')
        self.future = None
        self.pending_id = None
        self.closed = False
        self.notice = None
        self.setStyleSheet('QTableWidget {font-size:16px;} QPushButton {font-size:17px; min-height:42px;}')
        layout = QVBoxLayout(self)
        self.banner = QLabel('Ứng dụng cần chạy để nhắc đúng giờ. Máy tắt/ngủ: không thể nhắc hoặc gửi email.\n'
                            'Xác nhận là tự khai. Nếu lỡ liều, kiểm tra đơn hoặc hỏi bác sĩ/dược sĩ; không tự uống bù.')
        self.banner.setWordWrap(True)
        layout.addWidget(self.banner)
        self.policy = QLabel(f'Bắt kịp: không gửi email cũ quá {service.catchup_minutes} phút sau gián đoạn; '
                             f'tối đa {service.alert_limit} lượt gửi/giờ. Thử lại lỗi kết nối tối đa 3 lần.\n'
                             'Đóng cửa sổ là thoát; thu nhỏ vẫn nhắc. Chưa hỗ trợ khay hệ thống hoặc tự khởi động cùng Windows.')
        self.policy.setWordWrap(True)
        layout.addWidget(self.policy)
        controls = QHBoxLayout()
        self.filter = QComboBox()
        self.filter.addItem('Tất cả người dùng', None)
        self.filter.currentIndexChanged.connect(self.refresh)
        controls.addWidget(self.filter)
        self.view = QComboBox()
        self.view.addItems(['Hôm nay và đang đến giờ','Lịch sử và 7 ngày sắp tới'])
        self.view.currentIndexChanged.connect(self.refresh)
        controls.addWidget(self.view)
        for label, fn in [('Thêm lịch', self.add_schedule),('Sửa lịch',self.edit_schedule),('Ngừng lịch',self.disable_schedule),('Kiểm tra Gmail',self.test_email)]:
            b = QPushButton(label)
            b.clicked.connect(fn)
            controls.addWidget(b)
        layout.addLayout(controls)
        self.table = QTableWidget(0,7)
        self.table.setHorizontalHeaderLabels(['Người dùng','Thuốc / hướng dẫn','Giờ dự kiến','Trạng thái','Thao tác lúc','Email','Gián đoạn'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setDefaultSectionSize(72)
        layout.addWidget(self.table,1)
        self.details = QLabel('Chọn một dòng để đọc đầy đủ hướng dẫn.')
        self.details.setTextFormat(Qt.PlainText)
        self.details.setWordWrap(True)
        self.details.setStyleSheet('font-size:18px; padding:8px;')
        layout.addWidget(self.details)
        self.table.itemSelectionChanged.connect(self.show_details)
        actions = QHBoxLayout()
        for label, action in [('Đã uống (tự khai)','CONFIRMED'),('Nhắc lại sau','SNOOZED'),('Bỏ qua','SKIPPED')]:
            b = QPushButton(label)
            b.clicked.connect(lambda checked=False,a=action:self.act(a))
            actions.addWidget(b)
        layout.addLayout(actions)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(15000)
        self.result_timer = QTimer(self)
        self.result_timer.timeout.connect(self.poll_email)
        self.result_timer.start(500)
        self.rows = []
        self.refresh()
        QTimer.singleShot(0,self.tick)

    def refresh(self):
        selected = self.selected()
        chosen = selected['occurrence_id'] if selected else None
        current = self.filter.currentData()
        profiles = self.users.get_all_users()
        self.filter.blockSignals(True)
        self.filter.clear()
        self.filter.addItem('Tất cả người dùng',None)
        names = {}
        for u in profiles:
            uid = str(u.get('user_id') or u.get('patient_id'))
            names[uid] = u.get('name',uid)
            self.filter.addItem(f'{names[uid]} ({uid})',uid)
        self.filter.setCurrentIndex(max(0,self.filter.findData(current)))
        self.filter.blockSignals(False)
        self.rows = self.service.occurrences(self.filter.currentData())
        if self.view.currentIndex() == 0:
            self.rows = [d for d in self.rows if d['status'] in ('REMINDING','OVERDUE','SNOOZED') or
                         d['local_due_time'][:10] == self.service.clock().astimezone(ZoneInfo(d['timezone'])).date().isoformat()]
        self.table.setRowCount(len(self.rows))
        for i,d in enumerate(self.rows):
            values = [f"{names.get(d['patient_id'],d['patient_id'])} ({d['patient_id']})",
                      d['medication_name']+'\n'+d['instructions_text'],datetime.fromisoformat(d['local_due_time']).strftime('%H:%M\n%d/%m/%Y'),STATUS[d['status']],
                      datetime.fromisoformat(d['action_at']).astimezone(ZoneInfo(d['timezone'])).strftime('%H:%M:%S\n%d/%m/%Y') if d.get('action_at') else '',
                      EMAIL_STATUS.get(d['caregiver_alert_status'],d['caregiver_alert_status']),d.get('coverage','')]
            for j,value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(d['local_due_time'] if j == 2 else value)
                self.table.setItem(i,j,item)
            if chosen == d['occurrence_id']:
                self.table.selectRow(i)

    def show_details(self):
        d = self.selected()
        self.details.setText(f"{d['patient_id']} • {d['medication_name']} • {d['local_due_time']}\n{d['instructions_text']}" if d else 'Chọn một dòng để đọc đầy đủ hướng dẫn.')

    def selected(self):
        row = self.table.currentRow()
        return self.rows[row] if 0 <= row < len(getattr(self,'rows',[])) else None

    def add_schedule(self):
        self.edit(None)

    def edit_schedule(self):
        self.choose_schedule(False)

    def disable_schedule(self):
        self.choose_schedule(True)

    def choose_schedule(self, disable):
        from PyQt5.QtWidgets import QInputDialog
        schedules = [s for s in self.service.schedules() if s['active'] and (not self.filter.currentData() or s['patient_id']==self.filter.currentData())]
        labels = [f"{i+1}. {s['patient_id']} — {s['medication_name']} — {', '.join(s['local_times'])}" for i,s in enumerate(schedules)]
        if not labels:
            QMessageBox.information(self,'Lịch thuốc','Chưa có lịch đang hoạt động')
            return
        label, ok = QInputDialog.getItem(self,'Chọn lịch','Lịch cần thay đổi',labels,0,False)
        if not ok:
            return
        s = schedules[labels.index(label)]
        if disable:
            if QMessageBox.question(self,'Ngừng lịch',f"Ngừng các lần nhắc tương lai cho {s['medication_name']}? Lịch sử được giữ nguyên.") != QMessageBox.Yes:
                return
            s['active'] = False
            self.service.save_schedule(s,s['schedule_id'])
            self.refresh()
        else:
            self.edit(s)

    def edit(self, schedule):
        dlg = ScheduleDialog(self.users,schedule,self,clock=self.service.clock)
        if dlg.exec_() == QDialog.Accepted:
            self.service.save_schedule(dlg.result_data,schedule['schedule_id'] if schedule else None)
            self.refresh()

    def act(self, action):
        d = self.selected()
        if not d:
            QMessageBox.information(self,'Chọn lần nhắc','Hãy chọn đúng người và thuốc trong bảng')
            return
        text = f"{d['patient_id']} — {d['medication_name']}\n{d['local_due_time']}\n{STATUS[action]}?"
        if QMessageBox.question(self,'Xác nhận thao tác',text,QMessageBox.Yes|QMessageBox.No,QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            self.service.act(d['occurrence_id'],action)
            self.refresh()
        except ValueError as e:
            QMessageBox.warning(self,'Không thể thao tác',str(e))

    def tick(self):
        if self.closed:
            return
        try:
            reminders = self.service.tick()
            self.refresh()
            gaps = self.service.coverage()
            message = 'Đang kiểm tra lịch mỗi 15 giây. Gmail: '+('đã cấu hình' if self.email.is_configured() else 'chưa cấu hình')
            if gaps:
                message += f" | Không giám sát/đổi giờ: {gaps[0]['start']} → {gaps[0]['end']}"
            self.status.setText(message)
            if reminders:
                from PyQt5.QtWidgets import QApplication
                QApplication.beep()
                QApplication.alert(self.window(),10000)
                self.banner.setText(f'Có {len(reminders)} lần nhắc mới. Mở Nhắc uống thuốc để xác nhận đúng người và thuốc.\nỨng dụng cần chạy để nhắc đúng giờ; máy tắt/ngủ không phát nhắc.')
                if hasattr(self.window(),'medication_tabs'):
                    self.window().medication_tabs.setTabText(1,'Nhắc uống thuốc • Đến giờ')
                active = [d for d in self.service.occurrences() if d['status'] in ('REMINDING','OVERDUE')]
                lines = []
                for d in active:
                    profile = self.users.get_user_profile(d['patient_id']) or {}
                    lines.append(f"{profile.get('name',d['patient_id'])} ({d['patient_id']}) — {d['medication_name']}\n"
                                 f"{d['local_due_time']} • {d['instructions_text']}")
                if self.notice is None:
                    self.notice = QDialog(self)
                    self.notice.setWindowTitle('Đến giờ nhắc uống thuốc')
                    self.notice.resize(640,400)
                    notice_layout = QVBoxLayout(self.notice)
                    self.notice_text = QLabel()
                    self.notice_text.setTextFormat(Qt.PlainText)
                    self.notice_text.setWordWrap(True)
                    self.notice_text.setStyleSheet('font-size:20px;')
                    notice_layout.addWidget(self.notice_text)
                    open_page = QPushButton('Mở lịch uống thuốc')
                    def open_reminders():
                        if hasattr(self.window(),'medication_tabs'):
                            self.window().medication_tabs.setCurrentIndex(1)
                        self.window().showNormal()
                        self.window().raise_()
                        self.notice.hide()
                    open_page.clicked.connect(open_reminders)
                    notice_layout.addWidget(open_page)
                self.notice_text.setText('\n\n'.join(lines)+'\n\nMở trang Nhắc uống thuốc để xác nhận, nhắc lại hoặc bỏ qua.')
                self.notice.show()
                self.service.acknowledge_notifications(reminders)
            if self.future is None:
                d = self.service.claim_alert()
                if d:
                    self.pending_id = d['occurrence_id']
                    recipient = self.users.get_caregiver_email(d['patient_id']) or ''
                    profile = self.users.get_user_profile(d['patient_id']) or {}
                    self.future = self.pool.submit(self.email.send_medication_overdue,recipient,profile.get('name',d['patient_id']),d['local_due_time'],d['occurrence_id'])
        except Exception:
            self.status.setText('LỖI đọc/ghi lịch: không thể bảo đảm nhắc. Kiểm tra quyền ghi và dung lượng ổ đĩa.')

    def test_email(self):
        if self.future is None:
            self.pending_id = None
            self.future = self.pool.submit(self.email.test_connection)
            self.status.setText('Đang kiểm tra kết nối Gmail; thao tác này không gửi thư.')

    def poll_email(self):
        if self.future is not None and self.future.done():
            try:
                result = self.future.result()
                if self.pending_id:
                    self.service.finish_alert(self.pending_id,result)
                self.status.setText(result.message)
                self.refresh()
            except Exception:
                self.status.setText('Không rõ kết quả tác vụ email; không tự gửi lại.')
            self.future = None
            self.pending_id = None

    def shutdown(self):
        self.closed = True
        if self.notice is not None:
            self.notice.close()
        self.timer.stop()
        self.result_timer.stop()
        self.pool.shutdown(wait=True)
        self.poll_email()
        self.service.shutdown()
