# Custom Phone Agent

Ce projet est un agent vocal interactif basé sur FastAPI, Twilio, OpenAI et SendGrid. Il permet de lancer des appels téléphoniques automatisés, de transcrire et résumer les conversations, puis d'envoyer un résumé par email.

## Fonctionnalités principales

- Lancement d'appels téléphoniques personnalisés via une API REST
- Streaming audio en temps réel entre Twilio et OpenAI (WebSocket)
- Transcription et génération de réponses vocales par IA
- Résumé automatique de la conversation
- Envoi du résumé par email via SendGrid

## Fichiers principaux

- `custom-phone-agent.py` : point d'entrée principal, contient toute la logique serveur

## Prérequis et configuration Twilio/SendGrid

Avant de démarrer, vous devez disposer de :

- Un **compte Twilio** ([créer un compte](https://www.twilio.com/try-twilio))
- Un **numéro de téléphone Twilio** (acheté et vérifié sur la console Twilio)
- Un **compte SendGrid** ([créer un compte](https://signup.sendgrid.com/))
- Un **template dynamique d'email** créé dans SendGrid (pour le résumé, récupérez l'ID du template)
- Les **clés API** Twilio, OpenAI et SendGrid

Vous aurez besoin de ces informations pour remplir les variables d'environnement (voir plus bas).

---

## Dépendances

- Python 3.10+
- fastapi
- pydantic
- twilio
- websockets
- openai
- sendgrid
- python-dotenv
- redis

Installez-les avec :

```bash
pip install fastapi pydantic twilio websockets openai sendgrid python-dotenv redis
```

## Variables d'environnement nécessaires

- `TWILIO_ACCOUNT_SID` : SID du compte Twilio
- `TWILIO_AUTH_TOKEN` : Token d'authentification Twilio
- `PHONE_NUMBER_FROM` : Numéro Twilio émetteur
- `OPENAI_API_KEY` : Clé API OpenAI
- `SENDGRID_API_KEY` : Clé API SendGrid
- `CUSTOM_EMAIL_FROM` : Email expéditeur
- `SENDGRID_SUMMARY_TEMPLATE_ID` : ID du template SendGrid pour le résumé
- `CUSTOM_DOMAIN` : Domaine public pour le WebSocket (ex: `votre-domaine.com`), possible d'utiliser ngrok.

Utilisez un fichier `.env` pour stocker ces variables.

## Lancer le serveur

```bash
uvicorn custom-phone-agent:app --reload
```

## Description du endpoint API

### POST `/call_custom`

Déclenche un appel téléphonique automatisé avec un agent IA, puis envoie un résumé de la conversation par email à la fin de l'appel.

**Corps de la requête (JSON) :**

| Champ          | Type   | Description                                                                  |
| -------------- | ------ | ---------------------------------------------------------------------------- |
| phone_number   | string | Numéro de téléphone du destinataire (format international, ex: +33612345678) |
| email          | string | Adresse email pour recevoir le résumé                                        |
| system_message | string | Message système pour personnaliser le comportement de l'agent                |
| voice          | string | Voix IA à utiliser (ex: "alloy"). Voir ci-dessous pour la liste des voix.    |

**Liste des voix OpenAI**

Pour le paramètre `voice`, vous pouvez utiliser l'une des voix proposées par OpenAI (par exemple : `alloy`, `echo`, `fable`, `onyx`, `nova`, `shimmer`).

La liste complète et à jour des voix est disponible dans la documentation officielle : [OpenAI Text-to-Speech API Voices](https://platform.openai.com/docs/guides/text-to-speech/voice-options)

**Réponse (JSON) :**

```
{
  "message": "Call initiated to +33612345678",
  "session_id": "..."
}
```

---

## Exemple d'appel API

```json
POST /call_custom
{
  "phone_number": "+33612345678",
  "email": "destinataire@email.com",
  "system_message": "Vous êtes un agent téléphonique qui doit répondre aux questions qu'on vous pose. Ayez un ton positif et soyez courtois.",
  "voice": "alloy"
}
```

## Notes

- Ce projet utilise un stockage en mémoire pour les sessions (STREAMS_SETTINGS). Pour la production, utilisez Redis ou une base partagée.

## Auteur

Jeff Lemieux
