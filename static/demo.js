document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('upload-form');
  const overlay = document.getElementById('processing-overlay');
  const stepItems = document.querySelectorAll('#processing-steps li');
  const stepMessage = document.getElementById('step-message');
  const stallWarning = document.getElementById('stall-warning');
  const fileInput = document.getElementById('audio-input');
  const fileHint = document.getElementById('file-hint');
  const submitBtn = document.getElementById('submit-btn');
  const errorPanel = document.getElementById('error-panel');
  const errorText = document.getElementById('error-text');
  const retryBtn = document.getElementById('retry-btn');

  const SUPPORTED = ['.m4a', '.aac', '.mp3', '.mp4', '.wav', '.webm', '.mpeg', '.mpga', '.ogg'];
  const UPLOAD_TIMEOUT_MS = 5 * 60 * 1000;
  const STALL_WARNING_MS = 30 * 1000;
  const INITIAL_STEP_MESSAGE = 'ファイルをアップロードしています…';

  const STEP_MAP = {
    uploading: 1,
    transcribing: 2,
    evaluating: 3,
    reporting: 4,
    completed: 5,
    failed: 0,
  };

  let stallTimer = null;
  let isProcessing = false;

  function clearStallTimers() {
    if (stallTimer) {
      clearTimeout(stallTimer);
      stallTimer = null;
    }
    if (stallWarning) stallWarning.hidden = true;
  }

  function resetPageState() {
    clearStallTimers();
    isProcessing = false;

    if (overlay) overlay.hidden = true;
    if (errorPanel) errorPanel.hidden = true;
    if (stallWarning) stallWarning.hidden = true;
    if (submitBtn) submitBtn.disabled = false;
    document.body.style.overflow = '';

    stepItems.forEach((li) => {
      li.classList.remove('active', 'done');
    });
    if (stepMessage) stepMessage.textContent = INITIAL_STEP_MESSAGE;
  }

  function armStallWarning() {
    clearStallTimers();
    stallTimer = setTimeout(() => {
      if (stallWarning) stallWarning.hidden = false;
    }, STALL_WARNING_MS);
  }

  function showError(message) {
    clearStallTimers();
    isProcessing = false;
    if (errorPanel && errorText) {
      errorText.textContent = message;
      errorPanel.hidden = false;
      errorPanel.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
    hideOverlay();
    if (submitBtn) submitBtn.disabled = false;
  }

  function hideError() {
    if (errorPanel) errorPanel.hidden = true;
  }

  function showOverlay() {
    if (overlay) {
      overlay.hidden = false;
      document.body.style.overflow = 'hidden';
    }
  }

  function hideOverlay() {
    if (overlay) {
      overlay.hidden = true;
      document.body.style.overflow = '';
    }
  }

  function updateSteps(stepKey, message) {
    const current = STEP_MAP[stepKey] || 1;
    stepItems.forEach((li) => {
      const n = Number(li.dataset.step);
      li.classList.toggle('active', n === current && stepKey !== 'completed');
      li.classList.toggle('done', n < current || stepKey === 'completed');
    });
    if (stepMessage && message) {
      stepMessage.textContent = message;
    }
  }

  // 初回表示・リロード時は必ず初期状態に戻す（job復元・自動pollingなし）
  resetPageState();

  window.addEventListener('pageshow', (event) => {
    // ブラウザの戻る/進むで前回の処理中UIが復元された場合もリセット
    if (event.persisted && !isProcessing) {
      resetPageState();
    }
  });

  function validateFile(file) {
    if (!file) return 'ファイルを選択してください。';
    const name = file.name.toLowerCase();
    const ext = name.includes('.') ? name.slice(name.lastIndexOf('.')) : '';
    if (!SUPPORTED.includes(ext)) {
      return `非対応の形式です（${ext || '拡張子なし'}）。対応: m4a / aac / mp3 / mp4 / wav`;
    }
    if (file.size === 0) return 'ファイルが空です。';
    if (file.size > 100 * 1024 * 1024) return 'ファイルサイズが100MBを超えています。';
    return null;
  }

  function formatSize(bytes) {
    if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
    return `${(bytes / 1024).toFixed(0)}KB`;
  }

  function buildFormData(file) {
    const formData = new FormData();
    formData.append('staff_name', form.querySelector('[name="staff_name"]')?.value || '');
    formData.append('session_date', form.querySelector('[name="session_date"]')?.value || '');
    formData.append('audio', file, file.name);
    return formData;
  }

  function ensureFileReadable(file, timeoutMs = 15000) {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        reject(new Error(
          'ファイルの読み込みに時間がかかっています。iCloud上のファイルの場合はMacへダウンロードしてから再度お試しください。',
        ));
      }, timeoutMs);

      file.slice(0, 1).arrayBuffer()
        .then(() => {
          clearTimeout(timer);
          resolve();
        })
        .catch(() => {
          clearTimeout(timer);
          reject(new Error(
            'ファイルを読み込めません。iCloud上のファイルの場合はMacへダウンロードしてから再度お試しください。',
          ));
        });
    });
  }

  function uploadWithProgress(formData, file, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      let lastProgressAt = Date.now();

      xhr.upload.addEventListener('progress', (event) => {
        lastProgressAt = Date.now();
        if (event.lengthComputable) {
          const pct = Math.round((event.loaded / event.total) * 100);
          onProgress(pct, event.loaded, event.total);
        } else {
          onProgress(null, event.loaded, file.size);
        }
      });

      xhr.addEventListener('load', () => {
        let data = {};
        try {
          data = JSON.parse(xhr.responseText || '{}');
        } catch {
          reject(new Error('サーバー応答の解析に失敗しました'));
          return;
        }

        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(data);
          return;
        }

        reject(new Error(data.error || `アップロード失敗（HTTP ${xhr.status}）`));
      });

      xhr.addEventListener('error', () => {
        reject(new Error('ネットワークエラーです。サーバーが起動しているか確認してください。'));
      });

      xhr.addEventListener('timeout', () => {
        reject(new Error('アップロードがタイムアウトしました。ファイルサイズや形式を確認してください。'));
      });

      xhr.addEventListener('abort', () => {
        reject(new Error('アップロードが中断されました。'));
      });

      const progressWatch = setInterval(() => {
        if (Date.now() - lastProgressAt > STALL_WARNING_MS && stallWarning) {
          stallWarning.hidden = false;
        }
      }, 2000);

      xhr.timeout = UPLOAD_TIMEOUT_MS;
      xhr.open('POST', '/api/evaluate');
      xhr.onloadend = () => clearInterval(progressWatch);
      xhr.send(formData);
    });
  }

  if (fileInput && fileHint) {
    fileInput.addEventListener('change', () => {
      const file = fileInput.files?.[0];
      if (!file) {
        fileHint.textContent = 'タップして録音ファイルを選択（ボイスメモ可）';
        fileHint.style.color = '';
        return;
      }
      const err = validateFile(file);
      fileHint.textContent = err
        ? `⚠ ${err}`
        : `選択中: ${file.name}（${formatSize(file.size)}）`;
      fileHint.style.color = err ? '#b42318' : '';
    });
  }

  if (retryBtn) {
    retryBtn.addEventListener('click', () => {
      hideError();
      clearStallTimers();
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });
  }

  if (!form) return;

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    hideError();
    clearStallTimers();

    const file = fileInput?.files?.[0];
    const clientErr = validateFile(file);
    if (clientErr) {
      showError(clientErr);
      return;
    }

    showOverlay();
    isProcessing = true;
    updateSteps('uploading', 'ファイルを読み込んでいます…');
    if (submitBtn) submitBtn.disabled = true;
    armStallWarning();

    try {
      await ensureFileReadable(file);
    } catch (err) {
      showError(`アップロードに失敗しました：${err.message}`);
      return;
    }

    updateSteps('uploading', `ファイルをアップロードしています…（${formatSize(file.size)}）`);
    armStallWarning();

    let jobId;
    try {
      const formData = buildFormData(file);
      const data = await uploadWithProgress(formData, file, (pct, loaded, total) => {
        if (pct !== null) {
          updateSteps('uploading', `ファイルをアップロードしています… ${pct}%（${formatSize(loaded)} / ${formatSize(total)}）`);
        } else {
          updateSteps('uploading', `ファイルをアップロードしています… ${formatSize(loaded)} 送信済み`);
        }
        armStallWarning();
      });

      jobId = data.job_id;
      if (!jobId) {
        throw new Error('ジョブIDが返されませんでした');
      }

      updateSteps(
        data.step || 'transcribing',
        data.message || 'アップロード完了。文字起こしを開始します…',
      );
      clearStallTimers();
    } catch (err) {
      showError(`アップロードに失敗しました：${err.message}`);
      return;
    }

    let lastStep = 'transcribing';
    let lastStepChangeAt = Date.now();

    const poll = async () => {
      try {
        armStallWarning();

        const res = await fetch(`/api/jobs/${jobId}`);
        if (!res.ok) throw new Error('ジョブ状態の取得に失敗しました');
        const data = await res.json();

        if (data.step !== lastStep) {
          lastStep = data.step;
          lastStepChangeAt = Date.now();
          if (stallWarning) stallWarning.hidden = true;
        } else if (Date.now() - lastStepChangeAt > STALL_WARNING_MS && stallWarning) {
          stallWarning.hidden = false;
        }

        updateSteps(data.step, data.message);

        if (data.step === 'completed') {
          clearStallTimers();
          isProcessing = false;
          updateSteps('completed', '完了しました。レポートへ移動します…');
          setTimeout(() => {
            window.location.href = data.report_url;
          }, 800);
          return;
        }

        if (data.step === 'failed') {
          showError(`失敗しました。原因: ${data.error || data.message}`);
          return;
        }

        setTimeout(poll, 1500);
      } catch (err) {
        showError(`失敗しました。原因: ${err.message}`);
      }
    };

    poll();
  });
});
