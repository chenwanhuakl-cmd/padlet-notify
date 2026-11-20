# 哥哥的練習卷

考卷拍照 → Claude 出類似的題目 → 哥哥在平板上直接作答、自動批改。

## 資料夾
- `exams/` 你拍的考卷照片（給 Claude 看的，網頁不會讀）
- `quizzes/` 練習單的題目檔，一份一個 .json
- `quizzes/list.json` 列出有哪些練習單，網頁照這個讀

## 怎麼開
網頁要用網址開，不能直接點兩下檔案（`file://` 讀不到題目檔）。

    python -m http.server 8000

然後開 http://localhost:8000/practice/

## 題目檔格式

```json
{
  "id": "1120-math",
  "title": "數學 第三單元",
  "subject": "小一上 · 20 以內加減",
  "source": "11/20 隨堂考",
  "emoji": "➕",
  "questions": [
    { "type": "number", "tag": "加法", "q": "8 + 5 = ?",
      "say": "八加五等於多少？", "answer": 13, "hint": "八再往上數五個。" },

    { "type": "choice", "tag": "應用題", "q": "題目文字",
      "say": "念出來的話（數字要寫成國字，TTS 才念得順）",
      "sub": "題目下方的小提示", "choices": ["A", "B", "C"], "answer": 1,
      "hint": "答錯第二次時會念這句" }
  ]
}
```

- `type`：`number` 出數字鍵盤，`answer` 寫數字；`choice` 出選項按鈕，`answer` 是正確選項的索引（從 0 開始）
- `say` 一定要寫，哥哥看不懂字，全靠語音讀題
- 每題答錯兩次會直接告訴他答案並往下走，不會卡住他
- 寫完會列出錯題，可以一鍵複製給你看

## 新增一份練習單
1. 在 `quizzes/` 放 `xxx.json`
2. 把檔名加進 `quizzes/list.json` 的陣列
