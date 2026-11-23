# Guía de contribución

## Entorno de desarrollo

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   ·   macOS/Linux: source .venv/bin/activate
pip install -r backend/requirements-dev.txt
pre-commit install
```

`pre-commit install` registra un hook de git: antes de cada commit se ejecutan ruff (lint + formato) y validaciones básicas (YAML, espacios, archivos grandes, llaves privadas). Si un hook corrige algo, el commit se detiene: revisa los cambios, vuelve a agregarlos (`git add`) y repite el commit.

Para levantar el stack completo ver [README](README.md#puesta-en-marcha).

## Flujo de trabajo (GitHub Flow)

```
Issue → rama → commits → Pull Request → CI en verde → squash merge
```

1. **Issue primero.** Todo trabajo nace de un issue con criterios de aceptación. Asígnatelo y ubícalo en su milestone.
2. **Rama desde `main` actualizado:**
   ```bash
   git switch main && git pull
   git switch -c feature/12-video-upload
   ```
   Prefijos: `feature/`, `fix/`, `refactor/`, `docs/`, `chore/`, `ci/`. Incluye el número del issue.
3. **Commits pequeños** con [Conventional Commits](https://www.conventionalcommits.org/):

   | Tipo       | Uso                                         |
   |------------|---------------------------------------------|
   | `feat`     | Funcionalidad nueva                         |
   | `fix`      | Corrección de bug                           |
   | `refactor` | Cambio interno sin cambiar comportamiento   |
   | `test`     | Tests                                       |
   | `docs`     | Documentación                               |
   | `build`    | Docker, dependencias, empaquetado           |
   | `ci`       | GitHub Actions                              |
   | `chore`    | Mantenimiento                               |

   Scope opcional por área: `feat(api)`, `fix(worker)`, `build(nginx)`.
4. **Pull Request** contra `main`. El título también sigue Conventional Commits, porque se convierte en el commit final. En la descripción incluye `Closes #12`.
5. **CI obligatorio.** `main` está protegida: solo se mergea con los checks en verde.
6. **Squash merge** y la rama se borra automáticamente.

## Qué valida el CI

| Job                | Qué hace                                                                   |
|--------------------|----------------------------------------------------------------------------|
| `Lint`             | `ruff check` y `ruff format --check`                                       |
| `Test`             | `manage.py check`, `check --deploy` con `.env.production.example`, migraciones faltantes y suite de tests con PostgreSQL |
| `Stack smoke test` | Levanta todo con Docker Compose, valida `/api/health/` y una tarea del worker con FFmpeg |

Corre lo mismo en local antes de abrir el PR:

```bash
ruff check backend && ruff format --check backend
cd backend && python manage.py test
```

## Definición de terminado

- Criterios de aceptación del issue cumplidos
- Tests para la lógica nueva
- CI en verde
- Documentación actualizada si cambia setup, variables de entorno o endpoints

## Releases

Al cerrar cada fase se publica un release con versionado semántico (`v0.1.0`, `v0.2.0`, …). Las notas se generan automáticamente a partir de los PRs mergeados.
