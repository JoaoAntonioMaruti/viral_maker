# Viral Maker

CLI em Python para colocar uma legenda no primeiro vídeo e concatená-lo com um
vídeo final usando FFmpeg. O resultado é normalizado para 1080x1920, 60 fps e
H.264, adequado a vídeos verticais de redes sociais.

## Requisitos

- Python 3.10 ou superior
- FFmpeg e FFprobe
- Fonte `Noto Sans CJK JP` (incluída no pacote Noto CJK de várias distribuições)

## Uso

Com os padrões do projeto, escolhe uma legenda em inglês aleatoriamente:

```bash
python3 video_maker.py
```

O vídeo será salvo automaticamente em `outputs`, com um nome no formato
`{timestamp}_{video_number}.mp4`. Por exemplo: `outputs/20260915_143052_1.mp4`.
O número é obtido do nome do primeiro vídeo.

Selecionando idioma, frase e posição:

```bash
python3 video_maker.py --language pt --index 3 --position center
```

Todos os caminhos também podem ser alterados:

```bash
python3 video_maker.py \
  --first videos/1.mp4 \
  --final 2 \
  --final-directory videos/final_videos \
  --data data.json \
  --language ja \
  --position bottom
```

Os índices começam em 1. Sem `--index`, uma frase do idioma escolhido é
sorteada. O idioma padrão é `en` e a posição padrão é `top`. Use `--output`
para escolher outro caminho. Para substituir uma saída existente, passe
`--overwrite`.

Os vídeos finais ficam em `videos/final_videos` e usam nomes numéricos como
`1.mp4` e `2.mp4`. Selecione com `--final 2`; sem esse argumento, será usado o
final `1.mp4`.

## API HTTP

Instale as dependências e inicie o servidor:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn api:app --host 0.0.0.0 --port 8000
```

A documentação interativa estará disponível em `http://localhost:8000/docs`.

Crie um vídeo:

```bash
curl -X POST http://localhost:8000/videos \
  -H 'Content-Type: application/json' \
  -d '{"language":"pt","index":3,"position":"center","final":2}'
```

O processamento é síncrono: a resposta é enviada quando o vídeo estiver pronto.
Ela contém o identificador, a legenda selecionada e a URL para download. Consulte
`GET /videos/{id}` ou baixe com `GET /videos/{id}/download`. Consulte as opções
de vídeo final com `GET /finals`.

## Testes

```bash
python3 -m unittest -v
```
