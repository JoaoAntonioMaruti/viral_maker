# Viral Maker

CLI em Python para colocar uma legenda no primeiro vídeo e concatená-lo com um
vídeo final usando FFmpeg. O resultado é normalizado para 1080x1920, 60 fps e
H.264, adequado a vídeos verticais de redes sociais.
Todas as faixas de áudio dos vídeos de entrada são descartadas. Na execução
interativa, a CLI pergunta se deve adicionar música de fundo baixada de uma
URL do TikTok, com 100% de volume por padrão. Não há música padrão — sem uma
URL ou um arquivo explícito, o vídeo sai sem trilha sonora.

## Requisitos

- Python 3.10 ou superior
- FFmpeg e FFprobe
- ImageMagick (`magick`, usado para compor o texto com a seta do carrossel)
- yt-dlp (necessário para baixar áudio do TikTok)
- Fonte `Noto Sans CJK JP` (incluída no pacote Noto CJK de várias distribuições)
- Chromium gerenciado pelo Playwright (`make setup` instala automaticamente)

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
gerar sem música. Em scripts, use `--music-url URL` ou `--music arquivo.mp3`
para uma faixa já baixada, ou `--no-music`.
Controle o volume com `--music-volume`:

```bash
python3 video_maker.py --music-url 'https://vm.tiktok.com/exemplo/'
python3 video_maker.py --music audio/outra-musica.mp3 --music-volume 0.15
python3 video_maker.py --no-music
```

Para indicar que existe uma imagem no próximo item do carrossel, adicione o
texto localizado de `data.json` ao vídeo final. A indicação usa três cópias do
emoji de seta do iOS em `assets/right-arrow.png`, dimensionadas abaixo da altura
da fonte:

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

O caminho mais simples para iniciar a API em desenvolvimento é:

```bash
make dev
```

Outros comandos disponíveis:

```bash
make setup     # instala dependências
make api       # inicia sem recarregamento automático
make test      # executa os testes
make generate  # abre a geração interativa
make help      # lista os comandos
```

Alternativamente, instale as dependências e inicie o servidor manualmente:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn api:app --host 0.0.0.0 --port 8000
```

A documentação interativa estará disponível em `http://localhost:8000/docs`.

Crie um vídeo:

```bash
curl -X POST http://localhost:8000/videos/reaction \
  -H 'Content-Type: application/json' \
  -d '{"language":"pt","index":3,"position":"center","video":2,"final":2,"carousel":true,"carousel_position":"top","music":true,"music_filename":"7559553583222607107.mp3","music_volume":1.0,"npc_id":"ed9721fb-fa6c-4e66-85a0-c3dd0ba2a5df","clothes":"default","npc_name":"ハナ","description":"Descrição da cena","message":"Mensagem do diálogo","actions":["Primeira ação","Segunda ação"]}'
```

O processamento é síncrono: a resposta é enviada quando o vídeo estiver pronto.
Ela contém o identificador, a legenda selecionada e a URL para download. Consulte
`GET /videos/{id}` ou baixe com `GET /videos/{id}/download`. Consulte as opções
de vídeo inicial com `GET /videos`, de vídeo final com `GET /finals` e as músicas
disponíveis com `GET /audios`.

Quando `carousel` é `true`, a mesma chamada também gera o PNG do próximo slide.
Nesse caso, `npc_id`, `clothes`, `npc_name`, `description`, `message` e `actions`
são obrigatórios. A URL do cliente e o tamanho usam estes padrões:

```json
{
  "client_url": "http://127.0.0.1:3000/play",
  "screenshot_width": 540,
  "screenshot_height": 960
}
```

Os três campos podem ser alterados no payload. A resposta inclui
`screenshot_id` e `screenshot_url`; `GET /videos/{id}` também retorna essa
associação. O PNG pode ser baixado em `GET /screenshots/{screenshot_id}/download`.
Vídeo, screenshot e parâmetros de geração são associados no SQLite
`generation_assets.db`.

Consulte o histórico dos mocks de fala, do mais recente para o mais antigo:

```bash
curl http://localhost:8000/screenshots/history
```

Filtre pelo idioma (`en`, `pt` ou `ja`) com o parâmetro `language`:

```bash
curl 'http://localhost:8000/screenshots/history?language=ja'
```

Cada item contém os IDs e URLs do vídeo e screenshot, idioma, `npc_id`,
`clothes`, URL do cliente, dimensões, nome, descrição, mensagem, ações e data de
criação. Registros antigos, criados antes desse campo, retornam `language` como
`null`.

Liste os vídeos iniciais numerados:

```bash
curl http://localhost:8000/videos
```

Cada item contém `number`, `filename`, `size_bytes` e a `url` de streaming. A
listagem considera apenas arquivos como `videos/1.mp4`, sem incluir a subpasta
`videos/final_videos`. Passe o `number` escolhido no campo `video` de
`POST /videos/reaction`; se omitido, o vídeo inicial 1 será usado.

Exemplo da listagem de músicas:

```bash
curl http://localhost:8000/audios
```

Cada item contém `filename`, `size_bytes` e `url`; caminhos internos do
servidor não são retornados. O campo `url` aponta para o arquivo servido por HTTP.
Passe exatamente esse `filename` como `music_filename` em `POST /videos/reaction`.
Não existe música padrão: se `music_filename` for omitido, o vídeo sai sem
música mesmo com `music: true`.
Cada item também traz `views`, `likes`, `comments` e `shares` com as métricas de
engajamento do TikTok, quando conhecidas; esses campos são `null` quando o
arquivo não tem métricas associadas (arquivos baixados antes desta feature ou
adicionados manualmente).

Baixe um áudio do TikTok diretamente pela API, salvando o arquivo em `audio/`
e registrando as métricas de engajamento no SQLite local:

```bash
curl -X POST http://localhost:8000/audios \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.tiktok.com/@usuario/video/123"}'
```

A resposta (HTTP 201) é o mesmo formato de `GET /audios`, já com `views`,
`likes`, `comments` e `shares` preenchidos quando o TikTok expõe essas
métricas. Aceita `overwrite` (padrão `false`) e `cookies_from_browser` para
vídeos que exigem sessão autenticada. Um download inválido ou que falhe
retorna HTTP 400 com o motivo.

Liste as legendas filtrando pelo idioma:

```bash
curl 'http://localhost:8000/data?language=pt'
```

A resposta contém `language`, o texto `carousel` e a lista `data`. Os idiomas
aceitos são `en`, `pt` e `ja`.

Liste todos os vídeos gerados, do mais recente para o mais antigo:

```bash
curl http://localhost:8000/outputs
```

Cada item contém `id`, `filename`, `size_bytes`, `created_at`, `url` para
streaming, `download_url` e `generation`. O objeto `generation` registra no
SQLite os vídeos inicial e final, idioma, legenda selecionada, posições,
carousel e música usados na geração. Quando o vídeo possui um próximo slide,
também contém `screenshot_id` e `screenshot_url`. Arquivos antigos que não têm
registro no SQLite retornam `generation: null`.

`GET /videos/{id}` também retorna o objeto `generation` junto ao status e à
associação opcional com screenshot.

As pastas de mídia também são expostas diretamente, com suporte a streaming:

```text
GET /media/audios/{filename}
GET /media/videos/{path}
GET /media/outputs/{filename}
```

Exemplos:

```text
http://localhost:8000/media/audios/7559553583222607107.mp3
http://localhost:8000/media/videos/1.mp4
http://localhost:8000/media/videos/final_videos/2.mp4
http://localhost:8000/media/outputs/20260915_051435_1.mp4
```

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

## Screenshots de páginas web

Capture o viewport de uma página como PNG usando Chromium headless:

```bash
.venv/bin/python screenshot.py https://example.com \
  --width 1080 \
  --height 1920 \
  --npc-id NPC_ID \
  --output screenshot.png
```

O argumento `--output` é opcional. Quando omitido, o arquivo é criado em
`outputs/` seguindo o padrão de timestamp dos vídeos, por exemplo
`outputs/20260915_143052_screenshot.png`.

A captura usa supersampling 2x e mantém o PNG nas dimensões informadas. Isso
melhora a nitidez sem alterar o tamanho ou o layout dos componentes da página.

Também é possível executar um arquivo JavaScript depois do carregamento,
aguardar um seletor visível e aplicar um delay adicional em milissegundos:

```bash
.venv/bin/python screenshot.py https://example.com \
  --width 1080 \
  --height 1920 \
  --npc-id NPC_ID \
  --js inject.js \
  --wait-for '#marketing-card' \
  --delay 2000 \
  --output screenshot.png
```

Quando `--wait-for` e `--delay` são usados juntos, o seletor é aguardado
primeiro. A saída existente só é substituída quando `--overwrite` é informado.
O Chromium e o contexto da página são fechados automaticamente mesmo em caso
de erro.

Para acompanhar a automação com a janela do Chromium visível, adicione
`--headed`:

```bash
.venv/bin/python screenshot.py http://127.0.0.1:3000/play \
  --width 1080 \
  --height 1920 \
  --npc-id NPC_ID \
  --clothes default \
  --js assets/inject.js \
  --delay 2000 \
  --headed \
  --output screenshot.png
```

Os dados do chat mockado também podem ser substituídos pela CLI. Repita
`--action` para informar mais de uma ação:

```bash
.venv/bin/python screenshot.py http://127.0.0.1:3000/play \
  --width 540 \
  --height 960 \
  --npc-id NPC_ID \
  --clothes school_uniform \
  --npc-name 'ハナ' \
  --description 'Descrição da cena' \
  --message 'Mensagem do diálogo' \
  --action 'Primeira ação' \
  --action 'Segunda ação' \
  --js assets/inject.js \
  --headed
```

Sem esses campos opcionais, são usados os textos japoneses padrão definidos em
`screenshot.py`. A roupa padrão é `default`.

Se as dependências tiverem sido instaladas manualmente, instale também o
navegador usado pelo Playwright:

```bash
.venv/bin/playwright install chromium
```

## Testes

```bash
python3 -m unittest -v
```
