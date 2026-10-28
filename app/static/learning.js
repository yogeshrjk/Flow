/* Daily practice and listening use the same learner profile as conversation. */
(() => {
  let exercise = null, requestGen = 0;
  const el = id => document.getElementById(id);

  function escapeHtml(str) {
    if (typeof str !== 'string') return '';
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function icons() {
    if (window.lucide && typeof lucide.createIcons === 'function') {
      lucide.createIcons();
    }
  }

  async function api(path, payload) {
    const response = await fetch('/api/learning/' + path, payload === undefined ? {} : {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || data.error || 'Practice service unavailable');
    return data;
  }

  function status(text, type = 'info') {
    const s = el('learningStatus');
    if (!s) return;
    s.className = 'learning-status-banner ' + type;
    const icon = type === 'correct' ? 'check-circle-2' : type === 'wrong' ? 'alert-triangle' : type === 'error' ? 'alert-circle' : 'info';
    s.innerHTML = `<i data-lucide="${icon}"></i><span>${escapeHtml(text)}</span>`;
    icons();
  }

  async function refresh() {
    try {
      const plan = await api('plan');

      // Daily Progress Stats Grid
      const pCont = el('dailyProgress');
      if (pCont) {
        const spokenMin = Math.round((plan.today && plan.today.speaking_seconds || 0) / 60 * 10) / 10;
        const isEstimated = plan.assessment && plan.assessment.status === 'estimated';
        pCont.className = 'daily-stats-grid';
        pCont.innerHTML = `
          <div class="daily-stat-card">
            <div class="daily-stat-label"><i data-lucide="award"></i> CEFR LEVEL</div>
            <div class="daily-stat-value-row">
              <span class="daily-stat-val">${escapeHtml(plan.level || 'B1')}</span>
              <span class="daily-stat-badge ${isEstimated ? 'estimated' : 'provisional'}">${escapeHtml(plan.assessment ? plan.assessment.status : 'provisional')}</span>
            </div>
            <div class="daily-stat-sub" title="Focus: ${escapeHtml(plan.focus || 'Complete sentences')}">Focus: ${escapeHtml(plan.focus || 'Complete sentences')}</div>
          </div>
          <div class="daily-stat-card">
            <div class="daily-stat-label"><i data-lucide="mic"></i> SPOKEN TODAY</div>
            <div class="daily-stat-value-row">
              <span class="daily-stat-val">${spokenMin}</span>
              <span class="daily-stat-unit">min</span>
            </div>
            <div class="daily-stat-sub">Microphone input time</div>
          </div>
          <div class="daily-stat-card">
            <div class="daily-stat-label"><i data-lucide="book-marked"></i> VOCABULARY</div>
            <div class="daily-stat-value-row">
              <span class="daily-stat-val">${plan.observed_vocabulary || 0}</span>
              <span class="daily-stat-unit">words</span>
            </div>
            <div class="daily-stat-sub">Observed in active speech</div>
          </div>
        `;
      }

      // Daily Tasks List
      const list = el('dailyTasks');
      if (list) {
        list.className = 'daily-tasks-list';
        list.replaceChildren();
        const modeIcons = {
          free: 'message-square',
          reading: 'book-open',
          training: 'graduation-cap',
          roleplay: 'theater',
          listening: 'headphones'
        };
        const modeLabels = {
          free: 'Conversation',
          reading: 'Reading Practice',
          training: 'Targeted Training',
          roleplay: 'Roleplay Simulation',
          listening: 'Listening Practice'
        };
        (plan.tasks || []).forEach(task => {
          const card = document.createElement('button');
          card.type = 'button';
          card.className = `daily-task-card ${task.completed ? 'completed' : ''}`;
          const iconName = modeIcons[task.mode] || 'sparkles';
          const modeLabel = modeLabels[task.mode] || task.mode.toUpperCase();
          card.innerHTML = `
            <div class="daily-task-left">
              <div class="daily-task-icon"><i data-lucide="${iconName}"></i></div>
              <div class="daily-task-info">
                <span class="daily-task-title">${escapeHtml(task.title)}</span>
                <span class="daily-task-meta">${task.minutes} min · ${modeLabel}</span>
              </div>
            </div>
            <div class="daily-task-right">
              ${task.completed
                ? '<span class="daily-task-status done"><i data-lucide="check"></i> Completed</span>'
                : '<span class="daily-task-status start">Start <i data-lucide="arrow-right"></i></span>'}
            </div>
          `;
          card.onclick = () => {
            el('dailyPanel').classList.add('hidden');
            setLearningMode(task.mode);
          };
          list.append(card);
        });
      }

      // Recurring Mistakes Review Cards
      const reviews = el('dailyReviews');
      if (reviews) {
        reviews.className = 'daily-reviews-list';
        reviews.replaceChildren();
        (plan.reviews || []).forEach(card => {
          const row = document.createElement('div');
          row.className = 'daily-review-card';
          row.innerHTML = `
            <div class="daily-review-head">
              <div class="daily-review-icon"><i data-lucide="repeat"></i></div>
              <div class="daily-review-prompt">${escapeHtml(card.prompt)}</div>
            </div>
            <div class="daily-review-example">
              <span class="daily-review-example-label">Your recent speech</span>
              <p class="daily-review-example-quote">“${escapeHtml(card.example)}”</p>
            </div>
            <div class="daily-review-actions"></div>
          `;
          const actDiv = row.querySelector('.daily-review-actions');
          const btnSuccess = document.createElement('button');
          btnSuccess.type = 'button';
          btnSuccess.className = 'daily-review-btn success';
          btnSuccess.innerHTML = '<i data-lucide="check"></i> Practiced successfully';
          btnSuccess.onclick = async () => {
            try { await api('review', {card_id: card.id, correct: true}); await refresh(); }
            catch (e) { status(e.message, 'error'); }
          };
          const btnRetry = document.createElement('button');
          btnRetry.type = 'button';
          btnRetry.className = 'daily-review-btn retry';
          btnRetry.innerHTML = '<i data-lucide="rotate-ccw"></i> Need more practice';
          btnRetry.onclick = async () => {
            try { await api('review', {card_id: card.id, correct: false}); await refresh(); }
            catch (e) { status(e.message, 'error'); }
          };
          actDiv.append(btnSuccess, btnRetry);
          reviews.append(row);
        });
        if (!plan.reviews || !plan.reviews.length) {
          reviews.innerHTML = `
            <div class="daily-review-empty">
              <i data-lucide="check-circle-2"></i>
              <div>
                <p class="empty-title">All caught up on reviews!</p>
                <p class="empty-desc">Recurring mistakes will automatically generate spaced-repetition review cards as you practice speaking.</p>
              </div>
            </div>
          `;
        }
      }

      // Evidence Footnote
      const evEl = el('dailyEvidence');
      if (evEl) {
        evEl.className = 'daily-evidence-card pnote';
        evEl.innerHTML = `<i data-lucide="info"></i><span>${escapeHtml(plan.note || '')}</span>`;
      }

      // History Chips
      const history = (plan.history || []).slice(-7);
      const histEl = el('dailyHistory');
      if (histEl) {
        if (history.length) {
          histEl.className = 'daily-history-wrap';
          histEl.innerHTML = `
            <div class="daily-history-chips">
              ${history.map(d => `
                <div class="daily-history-chip">
                  <span class="daily-history-date">${d.date.length > 5 ? d.date.slice(5) : d.date}</span>
                  <span class="daily-history-words">${d.words || 0}w</span>
                  <span class="daily-history-patterns ${(d.mistakes && d.mistakes > 0) ? 'has-mistakes' : ''}">${d.mistakes || 0} ${d.mistakes === 1 ? 'pattern' : 'patterns'}</span>
                </div>
              `).join('')}
            </div>
          `;
        } else {
          histEl.className = 'daily-history-empty';
          histEl.innerHTML = '<i data-lucide="calendar"></i><span>Your progress history will appear here after practice.</span>';
        }
      }

      // Listening Score Badge
      const scEl = el('listeningScore');
      if (scEl) {
        scEl.className = 'listening-score-badge';
        scEl.innerHTML = plan.comprehension && plan.comprehension.attempts
          ? `<i data-lucide="check-circle"></i><span>Comprehension: <strong>${plan.comprehension.correct}/${plan.comprehension.attempts}</strong> correct</span>`
          : `<i data-lucide="headphones"></i><span>Listen first, then choose an answer</span>`;
      }

      // Settings Panel Daily Plan Preview Card
      const bpBadge = el('dailyPlanBadge');
      if (bpBadge) {
        bpBadge.textContent = `Level ${plan.level || 'B1'}`;
      }
      const bpSum = el('dailyPlanSummary');
      const allTasks = plan.tasks || [];
      const doneCount = allTasks.filter(t => t.completed).length;
      if (bpSum) {
        bpSum.textContent = `${doneCount} of ${allTasks.length} tasks completed · Focus: ${plan.focus || 'Complete sentences'}`;
      }
      const bpSpoken = el('dailyPlanSpoken');
      if (bpSpoken) {
        const spokenMin = Math.round((plan.today && plan.today.speaking_seconds || 0) / 60 * 10) / 10;
        bpSpoken.textContent = `${spokenMin} min`;
      }
      const bpDone = el('dailyPlanTasksDone');
      if (bpDone) {
        bpDone.textContent = `${doneCount} / ${allTasks.length}`;
      }
      const bpVocab = el('dailyPlanVocab');
      if (bpVocab) {
        bpVocab.textContent = `${plan.observed_vocabulary || 0} words`;
      }

      icons();
    } catch (e) {
      if (el('dailyProgress')) {
        el('dailyProgress').innerHTML = `<div class="daily-history-empty"><i data-lucide="alert-circle"></i><span>${escapeHtml(e.message)}</span></div>`;
      }
      if (el('dailyPlanSummary')) {
        el('dailyPlanSummary').textContent = 'Open to view plan & progress';
      }
      icons();
    }
  }

  async function newExercise() {
    const generation = ++requestGen;
    el('listeningAnswers').replaceChildren();
    el('listeningTranscript').textContent = '';
    if (el('listeningTranscriptWrap')) el('listeningTranscriptWrap').classList.add('hidden');
    if (el('listeningReveal')) el('listeningReveal').classList.remove('hidden');
    status('Preparing listening practice…', 'info');
    exercise = null;

    try {
      const data = await api('listening');
      if (generation !== requestGen || currentMode !== 'listening') return;
      exercise = data;
      el('listeningQuestion').textContent = data.question;

      const letters = ['A', 'B', 'C', 'D'];
      data.choices.forEach((choice, answer) => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'listening-choice-btn';
        const letter = letters[answer] || (answer + 1);
        btn.innerHTML = `
          <span class="choice-letter">${letter}</span>
          <span class="choice-text">${escapeHtml(choice)}</span>
          <span class="choice-mark"><i data-lucide="circle"></i></span>
        `;
        btn.onclick = async () => {
          for (const b of el('listeningAnswers').children) b.disabled = true;
          try {
            const result = await api('listening/answer', {exercise_id: data.id, answer});
            if (generation !== requestGen) return;
            btn.classList.add(result.correct ? 'is-correct' : 'is-wrong');
            const mark = btn.querySelector('.choice-mark');
            if (mark) {
              mark.innerHTML = result.correct ? '<i data-lucide="check-circle-2"></i>' : '<i data-lucide="x-circle"></i>';
            }
            status((result.correct ? 'Correct! ' : 'Keep practicing. ') + result.explanation, result.correct ? 'correct' : 'wrong');
            el('listeningTranscript').textContent = result.transcript;
            if (el('listeningTranscriptWrap')) el('listeningTranscriptWrap').classList.remove('hidden');
            if (el('listeningReveal')) el('listeningReveal').classList.add('hidden');
            refresh();
            icons();
          } catch (e) {
            status(e.message, 'error');
            for (const b of el('listeningAnswers').children) b.disabled = false;
          }
        };
        el('listeningAnswers').append(btn);
      });

      status('Play the audio clip. Try answering before revealing the transcript.', 'info');
      icons();
    } catch (e) {
      status(e.message, 'error');
    }
  }

  function showMode(mode) {
    el('listeningPanel').classList.toggle('hidden', mode !== 'listening');
    const stage = document.querySelector('.stage');
    if (stage) stage.classList.toggle('listening-on', mode === 'listening');
    if (mode === 'listening') newExercise();
    else requestGen++;
  }

  el('dailyPlanBtn').onclick = () => { el('dailyPanel').classList.remove('hidden'); refresh(); };
  el('dailyClose').onclick = () => el('dailyPanel').classList.add('hidden');

  if (el('dailyPanel')) {
    el('dailyPanel').onclick = (e) => {
      if (e.target === el('dailyPanel')) el('dailyPanel').classList.add('hidden');
    };
  }

  if (el('listeningClose')) {
    el('listeningClose').onclick = () => setLearningMode('free');
  }

  el('placementSubmit').onclick = async () => {
    const textVal = el('placementText').value.trim();
    if (!textVal) {
      const s = el('placementStatus');
      s.className = 'placement-status-msg error';
      s.innerHTML = '<i data-lucide="alert-circle"></i><span>Please write an answer before submitting.</span>';
      icons();
      return;
    }
    try {
      const a = await api('assess', {text: textVal});
      const s = el('placementStatus');
      s.className = 'placement-status-msg success';
      s.innerHTML = `<i data-lucide="check-circle-2"></i><span>Level: <strong>${escapeHtml(a.level)}</strong> (${escapeHtml(a.status)}). ${a.samples} sample(s) collected; aim for 3 answers and 100 words total.</span>`;
      el('placementText').value = '';
      refresh();
      icons();
    } catch (e) {
      const s = el('placementStatus');
      s.className = 'placement-status-msg error';
      s.innerHTML = `<i data-lucide="alert-circle"></i><span>${escapeHtml(e.message)}</span>`;
      icons();
    }
  };

  el('listeningPlay').onclick = async () => {
    if (!exercise) return;
    audioQ.cancel('listening_replay');
    await audioQ.unlock();
    audioQ.startTurn('listen_' + Date.now());
    audioQ.enqueue(exercise.text, {stream: true, rate: Number(el('listeningSpeed').value), turn: audioQ._turnId});
  };

  el('listeningReveal').onclick = () => {
    if (exercise) {
      el('listeningTranscript').textContent = exercise.text;
      if (el('listeningTranscriptWrap')) el('listeningTranscriptWrap').classList.remove('hidden');
      el('listeningReveal').classList.add('hidden');
    }
  };

  el('listeningNext').onclick = newExercise;

  window.FlowLearning = {showMode, refresh};
  showMode(currentMode);
  refresh();
})();
