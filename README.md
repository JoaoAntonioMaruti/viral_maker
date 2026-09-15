# Viral Maker

CLI em Python para colocar uma legenda no primeiro vídeo e concatená-lo com um
vídeo final usando FFmpeg. O resultado é normalizado para 1080x1920, 60 fps e
H.264, adequado a vídeos verticais de redes sociais.
Todas as faixas de áudio dos vídeos de entrada são descartadas. Na execução
interativa, a CLI pergunta se deve adicionar a música padrão de `audio/` ao
fundo, com 100% de volume.

## Requisitos

- Python 3.10 ou superior
- FFmpeg e FFprobe
- yt-dlp (necessário para baixar áudio do TikTok)
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

Responda `Y` ou pressione Enter para adicionar música; a CLI pedirá uma URL do
TikTok, baixará o MP3 em `audio/` e o usará na mesma montagem. Responda `n` para
gerar sem música. Em scripts, use `--music-url URL`, `--with-music` para a faixa
padrão já baixada, ou `--no-music`.
Controle o volume com `--music-volume` ou use outro arquivo com `--music`:

```bash
python3 video_maker.py --music-url 'https://vm.tiktok.com/exemplo/'
python3 video_maker.py --with-music --music-volume 0.15
python3 video_maker.py --music audio/outra-musica.mp3
python3 video_maker.py --no-music
```

Para indicar que existe uma imagem no próximo item do carrossel, adicione o
texto localizado de `data.json` ao vídeo final:

```bash
python3 video_maker.py --final 2 --carousel --carousel-position top
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
  -d '{"language":"pt","index":3,"position":"center","final":2,"carousel":true,"carousel_position":"top","music":true,"music_volume":1.0}'
```

O processamento é síncrono: a resposta é enviada quando o vídeo estiver pronto.
Ela contém o identificador, a legenda selecionada e a URL para download. Consulte
`GET /videos/{id}` ou baixe com `GET /videos/{id}/download`. Consulte as opções
de vídeo final com `GET /finals`.

## Baixar áudio do TikTok

Use somente vídeos próprios ou conteúdo que você tenha autorização para baixar.
O comando mostra porcentagem, velocidade e tempo restante durante o download,
extrai o áudio em MP3 e salva em `audio/{tiktok_id}.mp3`:

```bash
python3 download_tiktok_audio.py 'https://www.tiktok.com/@usuario/video/123'
```

Para vídeos que exigem uma sessão autenticada, importe os cookies do navegador:

```bash
python3 download_tiktok_audio.py URL --cookies-from-browser firefox
```

Depois, use o caminho exibido pelo comando na montagem:

```bash
python3 video_maker.py --music audio/123.mp3
```

## Testes

```bash
python3 -m unittest -v
```
