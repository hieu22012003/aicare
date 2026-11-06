"""Stateful per-person LSTM from HumanFallDetection (MIT).

Copyright (c) 2020 Mohammad Taufeeque and Samad Koita.
See HumanFallDetection/LICENSE for the complete notice.
"""
from collections import deque
from dataclasses import dataclass, field
import hashlib
from pathlib import Path

import numpy as np
import torch
from torch import nn
from features.fall_risk.hfd_model.hfd_features import feature_vector, pose_sample


class LSTMModel(nn.Module):
    def __init__(self):
        super().__init__()
        self.LSTM=nn.LSTM(5,48,2,dropout=.1,batch_first=True)
        self.fc1=nn.Linear(48,7)

    def forward(self,x,h_s=None):
        sequence,state=self.LSTM(x,h_s)
        return self.fc1(sequence[:,-1,:]),state


@dataclass
class SequenceState:
    hidden: object = None
    history: object = field(default_factory=lambda:deque(maxlen=3))
    steps: int = 0
    first_pts: float = 0.


class HfdClassifier:
    def __init__(self,weights=None,max_gap=.25,warmup_seconds=1.,min_steps=3):
        path=Path(weights or Path(__file__).parent/'models/hfd_lstm.sav').resolve()
        if not path.is_file():raise FileNotFoundError(f'HFD LSTM weights missing: {path}')
        torch.set_num_threads(4)
        self.model=LSTMModel().eval()
        self.model.load_state_dict(torch.load(path,map_location='cpu',weights_only=True),strict=True)
        self.metadata=dict(classifier='hfd-lstm',sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                           feature_schema='hfd-legacy-v1',classes=7,fall_class=0,
                           label_map_status='upstream-inference-map; training labels NOT VERIFIED',
                           score_calibration='NOT VERIFIED',cadence='source PTS; training cadence NOT VERIFIED')
        self.states={};self.max_gap=max_gap;self.warmup_seconds=warmup_seconds;self.min_steps=min_steps

    def reset_track(self,track_id):self.states.pop(track_id,None)

    def retain(self,track_ids):
        for tid in list(self.states):
            if tid not in track_ids:self.reset_track(tid)

    def observe(self,track_id,observation):
        sample=pose_sample(observation)
        if sample is None or observation.identity_uncertain:
            self.reset_track(track_id)
            return dict(valid=False,reason='Missing measured H/N/B or ambiguous track')
        state=self.states.get(track_id)
        if state and (sample['time']-state.history[-1]['time']>self.max_gap or sample['time']<=state.history[-1]['time']):
            self.reset_track(track_id);state=None
        if state is None:
            state=SequenceState(first_pts=sample['time']);self.states[track_id]=state
        try:
            vector=feature_vector(state.history,sample)
        except (ValueError, ZeroDivisionError, OverflowError):
            self.reset_track(track_id)
            return dict(valid=False,reason='Invalid/non-finite HFD feature history')
        with torch.inference_mode():
            logits,hidden=self.model(torch.from_numpy(vector).reshape(1,1,5),state.hidden)
        values=logits[0].numpy()
        if not np.isfinite(values).all():
            self.reset_track(track_id)
            return dict(valid=False,reason='Non-finite HFD logits')
        state.hidden=tuple(v.detach() for v in hidden)
        state.history.append(sample);state.steps+=1
        scores=torch.softmax(logits,dim=1)[0].numpy()
        ready=state.steps>=self.min_steps and sample['time']-state.first_pts>=self.warmup_seconds
        return dict(valid=True,ready=ready,steps=state.steps,class_id=int(values.argmax()),
                    fall_score=float(scores[0]),logits=values.tolist(),features=vector.tolist(),
                    angle_vertical=sample['features']['angle_vertical'],feature_schema='hfd-legacy-v1',
                    reason='Ready' if ready else 'Model warm-up')

    def close(self):self.states.clear()
