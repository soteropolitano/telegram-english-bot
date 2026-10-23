# Telegram English Bot


[![Capa do Telegram English Bot](linkedin-cover-telegram-english-bot.jpg)](linkedin-cover-telegram-english-bot.jpg)

[Prévia para o LinkedIn](https://soteropolitano.github.io/telegram-english-bot/linkedin-preview-v2/)

Bot no Telegram para ajudar em estudos de inglês. Ele corrige frases, transcreve áudios curtos e explica os erros em português, sugerindo uma versão mais natural em inglês.

## Visão geral

O projeto é um webhook no Telegram para ajudar em estudos de inglês. Ele corrige frases, transcreve áudios curtos e explica os erros em português, sugerindo uma versão mais natural em inglês.

A arquitetura roda em Python com Flask no Google Cloud Run, usando webhook do Telegram, Gemini API para correção/transcrição, Secret Manager para credenciais e Cloud Storage para persistir o controle mensal de custo.

Além da funcionalidade principal, implementei controle de acesso, limite de áudio, uso em grupo apenas com comando, saída automática de grupos não autorizados e hard stop interno para evitar gasto inesperado.

## Funcionalidades

- Correção de texto em inglês
- Transcrição e correção de áudios curtos
- Explicação dos erros em português
- Sugestão de versão mais natural em inglês
- Comando `/corrigir` para uso em grupos
- Comandos `/start` e `/help` no privado
- Owner gate para restringir uso privado
- Saída automática de grupos não autorizados
- Limite de áudio por tamanho e duração
- Controle mensal de custo em Cloud Storage
- Hard stop interno para evitar gasto inesperado

## Arquitetura

```text
Telegram
   ↓ Webhook
Cloud Run
   ↓
Python + Flask + Gunicorn
   ↓
Gemini API
   ↓
Resposta no Telegram

Cloud Storage
   ↓
Ledger mensal de custo estimado

Secret Manager
   ↓
Tokens e chaves sensíveis
