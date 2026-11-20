/* 從 ../../flashcards/index.html 的單字清單，自動產生每個單元的練習卷。
   用法： node practice/tools/make_quiz.js
   題型：看圖選字 / 聽音選字 / 看字選圖 / 字母填空（自然發音）  */
const fs = require('fs'), path = require('path');

const ROOT = path.resolve(__dirname, '..', '..');
const html = fs.readFileSync(path.join(ROOT, 'flashcards', 'index.html'), 'utf8');
const UNITS = eval(html.match(/const UNITS = \[[\s\S]*?\n\];/)[0].replace(/^const UNITS = /, '').replace(/;\s*$/, ''));

// 固定種子的亂數，這樣每次重跑結果一樣，git diff 才乾淨
let seed = 20261120;
const rnd = () => (seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
const shuffle = a => { a = a.slice(); for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(rnd() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; } return a; };
const pick = (a, n) => shuffle(a).slice(0, n);

const VOWELS = ['a', 'e', 'i', 'o', 'u'];
const GROUP_INITIAL = { 'u4-g': 'g', 'u4-h': 'h', 'u5-i': 'i', 'u5-j': 'j', 'u6-k': 'k', 'u6-l': 'l' };
const UNIT_META = {
  u4: { emoji: '🎁', subject: 'Unit 4 · 人物、G / H 自然發音' },
  u5: { emoji: '🔢', subject: 'Unit 5 · 數字、I / J 自然發音' },
  u6: { emoji: '👑', subject: 'Unit 6 · 形容詞、K / L 自然發音' }
};

function buildQuestion(word, set, unit, kind) {
  const pool = unit.sets.reduce((a, s) => a.concat(s.words), []).filter(w => w.en !== word.en);
  const sameSet = set.words.filter(w => w.en !== word.en);
  const distractPool = sameSet.length >= 2 ? sameSet : pool;

  if (kind === 'blank') {
    const initial = GROUP_INITIAL[set.id];
    let idx, correct, others;
    if (initial && word.en[0].toLowerCase() === initial) {
      idx = 0;
      correct = word.en[0];
      const all = Object.values(GROUP_INITIAL).filter(c => c !== correct.toLowerCase());
      others = pick(all, 2);
    } else {
      const positions = word.en.split('').map((c, i) => VOWELS.indexOf(c.toLowerCase()) > -1 ? i : -1).filter(i => i > 0);
      idx = positions.length ? positions[0] : 1;
      correct = word.en[idx];
      others = pick(VOWELS.filter(v => v !== correct.toLowerCase()), 2);
    }
    const show = word.en.split('').map((c, i) => i === idx ? '_' : c).join('');
    const choices = shuffle([correct].concat(others));
    return { ask: 'blank', opt: 'letter', tag: '拼拼看', word: word.en, show: show,
             choices: choices, answer: choices.indexOf(correct) };
  }

  const others = pick(distractPool, 2).map(w => w.en);
  const choices = shuffle([word.en].concat(others));
  const answer = choices.indexOf(word.en);
  if (kind === 'pic')    return { ask: 'pic',    opt: 'text', tag: '看圖選字', word: word.en, choices: choices, answer: answer };
  if (kind === 'listen') return { ask: 'listen', opt: 'text', tag: '聽音選字', word: word.en, choices: choices, answer: answer };
  return                        { ask: 'word',   opt: 'pic',  tag: '看字選圖', word: word.en, choices: choices, answer: answer };
}

const KINDS = ['pic', 'listen', 'word', 'blank'];
const names = [];

UNITS.forEach(unit => {
  const meta = UNIT_META[unit.id] || { emoji: '📝', subject: unit.name };
  const words = unit.sets.reduce((a, s) => a.concat(s.words.map(w => ({ w: w, s: s }))), []);
  const questions = shuffle(words).map((x, i) => buildQuestion(x.w, x.s, unit, KINDS[i % KINDS.length]));
  const quiz = {
    id: unit.id + '-mix',
    title: unit.name + ' 綜合練習',
    subject: meta.subject,
    source: '依字卡自動產生',
    emoji: meta.emoji,
    questions: questions
  };
  const file = unit.id + '-mix.json';
  fs.writeFileSync(path.join(ROOT, 'practice', 'quizzes', file), JSON.stringify(quiz, null, 2) + '\n', 'utf8');
  names.push(file);
  console.log(file, '->', questions.length, '題');
});

fs.writeFileSync(path.join(ROOT, 'practice', 'quizzes', 'list.json'), JSON.stringify(names, null, 2) + '\n', 'utf8');
console.log('list.json ->', names.join(', '));
