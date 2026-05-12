# Dashboard de Imagens

Aplicação Python sem dependências externas para indexar um diretório de imagens/vídeos em SQLite e exibir um relatório por veículo, câmera e dia.

## Estrutura Esperada

```text
/home/publico/imagens/
  veiculo_1/
    camera1/
      2026-04-16/
        20260416083001.mp4
        20260416093001.jpg
```

## Como Executar

No Windows:

```powershell
$env:IMAGE_DASHBOARD_ROOT="/home/publico/imagens"
python app.py
```

No Linux:

```bash
export IMAGE_DASHBOARD_ROOT=/home/publico/imagens
export IMAGE_DASHBOARD_DB=/home/publico/dashboard_imagens.db
python3 app.py
```

Depois acesse:

```text
http://localhost:8081
```

## Configuração

Veja [.env.example](.env.example) para as variáveis disponíveis.

Principais opções:

```bash
export IMAGE_DASHBOARD_PORT=8081
export IMAGE_DASHBOARD_AUTO_SCAN=1
export IMAGE_DASHBOARD_SCAN_INTERVAL_SECONDS=300
export IMAGE_DASHBOARD_DURATION_INTERVAL_SECONDS=300
export IMAGE_DASHBOARD_ENABLE_DURATION=1
```

Use `0` em `IMAGE_DASHBOARD_SCAN_INTERVAL_SECONDS` ou `IMAGE_DASHBOARD_DURATION_INTERVAL_SECONDS` para desativar os agendadores.

## Rotas

- `/` interface web
- `/api/dashboard` dados JSON do relatório
- `/api/rescan` inicia uma indexação incremental em background
- `/api/rescan?full=1` executa reindexação completa
- `/api/scan-status` consulta o status da indexação
- `/api/duration-status` consulta o status da fila de duração dos vídeos
- `/health` healthcheck simples

## Funcionamento

- A indexação incremental inicia automaticamente junto com a aplicação.
- A indexação incremental roda novamente a cada 5 minutos por padrão.
- A tela lê dados do SQLite e não varre o filesystem a cada carregamento.
- A duração dos vídeos é preenchida em background quando `ffprobe` está disponível.
- Veículos em alerta são aqueles sem imagens há 3 dias ou mais, ou sem nenhuma imagem registrada.

## Observações Para Git

- Bancos SQLite, caches Python, logs e `.env` ficam fora do Git via `.gitignore`.
- O projeto não exige `requirements.txt` porque usa apenas biblioteca padrão do Python.
- Para calcular duração de vídeos, instale `ffmpeg/ffprobe` no servidor.
