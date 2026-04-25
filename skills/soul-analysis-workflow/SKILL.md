---
name: soul-analysis-workflow
description: 一元化済みログと手動収集した Web AI ログを使って Soul Analysis 用パッケージを作るスキル。
---

# Soul Analysis Workflow

`AI-Nikki` 内の別ワークフローです。  
日記生成とは別目的ですが、同じ一元化 DB を利用できます。

## ゴール

1. 既存のローカル AI ログを読む
2. AI ごとの分析パケットを作る
3. Web AI 用の手動投入ガイドを作る
4. 最終統合用のプロンプトを作る

## 重要

- 1 source しかなくても、その範囲で実行してよい
- 無いログを「ある」と扱わない
- Web 側ログは自動取得したことにしない
