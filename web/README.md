# Master Data — web

Next.js (App Router). Todas las llamadas del navegador pasan por el proxy
`app/api/backend`, que añade el token desde una cookie HttpOnly; solo las
cargas de archivos van directo a la API (`NEXT_PUBLIC_API_URL`).

```bash
npm ci
NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev   # http://localhost:3000
```

| Comando              | Qué hace                                  |
| -------------------- | ----------------------------------------- |
| `npm run lint`       | ESLint                                    |
| `npm run typecheck`  | `tsc --noEmit`                            |
| `npm test`           | Vitest (unitarias, `tests/unit`)          |
| `npm run test:e2e`   | Playwright (extremo a extremo, `tests/e2e`) |

## Pruebas end-to-end

Playwright NO levanta nada: la base, la API y la web tienen que estar
corriendo antes. Si la API no responde en `$MASTERDATA_API_URL/health`, cada
spec se salta sola en vez de fallar. Nunca contra producción: la suite crea
cuentas, empresas y cargas de verdad.

1. **PostgreSQL desechable** (cualquiera sirve; con Docker):

   ```bash
   docker run -d --rm --name masterdata-e2e -e POSTGRES_USER=norte \
     -e POSTGRES_DB=norte -e POSTGRES_PASSWORD=e2e-pg -p 5439:5432 postgres:16-alpine
   ```

2. **Migraciones, roles y datos demo** (desde la raíz del repo, igual que CI):

   ```bash
   export POSTGRES_ADMIN_URL=postgresql://norte:e2e-pg@localhost:5439/norte
   export POSTGRES_APP_PASSWORD=e2e-app POSTGRES_READONLY_PASSWORD=e2e-ro
   python -m scripts.migrate && python -m scripts.setup_roles
   export DATABASE_URL=postgresql://norte_app:e2e-app@localhost:5439/norte
   export JWT_SECRET=$(openssl rand -hex 32) PII_HASH_SALT=$(openssl rand -hex 32) \
          WORKER_TRIGGER_SECRET=$(openssl rand -hex 32)
   python -m scripts.seed_demo --reset      # demo@masterdata.app / demo-masterdata-2026
   ```

3. **API**, en la misma terminal:

   ```bash
   export CONNECTION_VAULT_KEY=$(python -m scripts.generate_vault_key | cut -d= -f2-)
   export RATE_LIMIT_AUTH_PER_MINUTE=1000   # la suite entra ~40 veces por minuto
   export CORS_ORIGINS=http://localhost:3000 AI_ENABLED=false
   uvicorn api.main:app --port 8000
   ```

4. **Web**, en otra terminal: `NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev`
   (o `npm run build && npm start` para probar el build de producción).

5. **Correr la suite:**

   ```bash
   npx playwright install chromium      # la primera vez
   MASTERDATA_API_URL=http://localhost:8000 PLAYWRIGHT_BASE_URL=http://localhost:3000 \
     npm run test:e2e
   ```

Qué cubre (`tests/e2e`):

- `critical-path` — login demo, tablero con widget bloqueado, `/ingest` → carga.
- `account` — registro → primera empresa → conexiones, logout, contraseña
  errónea, access token vencido renovado con el refresh, cambio de contraseña.
- `upload` — reporte de guías hasta «Listo», duplicado, reporte de dinero
  elegido como «Guías», datos en el tablero y en órdenes.
- `dashboard` — Global con rango de fechas, filtros de estados y plataforma,
  Informe (dice qué estados quedaron fuera), arrastrar tarjetas y
  «Restablecer el orden», país que la empresa no tiene.
- `catalog` — búsqueda de guías y productos con `%` y `_` literales, clientes,
  edición del costo de un producto.
- `users` — invitar un socio de solo lectura limitado a un país, aceptar la
  invitación, cambiar el rol y quitar el acceso.
- `connections` — token de Dropi falso y código de la extensión de Effi (canje
  por POST directo con una `ci_session` falsa). Estos dos salen a internet:
  Dropi y Effi rechazan las credenciales falsas y la pantalla debe decirlo.
- `pages-health` — cada pantalla sin errores de consola, excepciones ni
  peticiones fallidas, en escritorio y a 390 px sin desbordarse a lo ancho.

Cada spec crea su propia cuenta con un correo `@example.com` único, así que
se puede correr varias veces sobre la misma base. Con `next dev` la primera
visita a cada ruta compila; el límite por prueba es de 90 s por eso.
