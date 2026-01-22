# 🚂 Migración de Vercel a Railway

## 🎯 Resumen

**Problema actual**: Vercel Free tiene timeout de 10 segundos, pero Oxylabs tarda 10-30 segundos en responder.

**Solución**: Migrar a Railway con timeout de 120 segundos.

## 📦 Archivos Creados

He creado todos los archivos necesarios para Railway en `upc-backend-clean/`:

1. **`app.py`** - Flask application (reemplaza BaseHTTPRequestHandler de Vercel)
2. **`requirements-railway.txt`** - Dependencias Flask + gunicorn
3. **`Procfile`** - Comando de inicio con timeout 120s
4. **`runtime.txt`** - Python 3.11.6
5. **`railway.json`** - Configuración Railway
6. **`RAILWAY_DEPLOYMENT.md`** - Guía detallada de deployment
7. **`test_local.py`** - Script para probar localmente

## 🚀 Pasos Rápidos (5 minutos)

### 1. Crear proyecto en Railway

```bash
# 1. Ve a https://railway.app/
# 2. Login con GitHub
# 3. "New Project" → "Deploy from GitHub repo"
# 4. Selecciona tu repo: upc-price-finder_v4_chatbot
```

### 2. Configurar en Railway Dashboard

**Settings → General**:
- Root Directory: `upc-backend-clean`

**Settings → Variables**:
```
GEMINI_KEY=tu_gemini_key
OXYLABS_USERNAME=sdatabunker
OXYLABS_PASSWORD=sDatabunker=123
PORT=5000
```

**Settings → Deploy**:
- Build Command: `pip install -r requirements-railway.txt`
- Start Command: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120`

### 3. Deploy

Railway hará deploy automáticamente. Espera 2-3 minutos.

### 4. Obtener URL

Railway te dará una URL como: `https://tu-app-production-xxxx.up.railway.app`

### 5. Probar

```bash
# Health check
curl https://tu-app-production-xxxx.up.railway.app/health

# Debug
curl https://tu-app-production-xxxx.up.railway.app/api/debug

# Price check
curl -X POST https://tu-app-production-xxxx.up.railway.app/api/check_price \
  -H "Content-Type: application/json" \
  -d '{"query":"Coca Cola 600ml","search_type":"shopping"}'
```

### 6. Actualizar Extension

Edita `upc-extension-react/background/background.js`:

```javascript
// Línea 10
const DEFAULT_BACKEND_URL = 'https://tu-app-production-xxxx.up.railway.app';
```

Recarga la extensión en Chrome.

## 💰 Costos

| Plan | Costo | Límites |
|------|-------|---------|
| **Railway Starter** | $5 gratis/mes | Luego ~$0.000231/GB-min |
| Estimado mensual | **$5-10/mes** | Con uso moderado |
| **Vercel Pro** | $20/mes | Para timeout 60s |

**Conclusión**: Railway es **50% más barato** que Vercel Pro.

## 🔧 Prueba Local (Opcional)

Antes de hacer deploy, puedes probar localmente:

```bash
cd upc-backend-clean

# Instalar dependencias
pip install -r requirements-railway.txt

# Configurar variables de entorno (Windows)
set GEMINI_KEY=tu_key
set OXYLABS_USERNAME=sdatabunker
set OXYLABS_PASSWORD=sDatabunker=123

# Configurar variables de entorno (Mac/Linux)
export GEMINI_KEY=tu_key
export OXYLABS_USERNAME=sdatabunker
export OXYLABS_PASSWORD=sDatabunker=123

# Ejecutar servidor
python app.py

# En otra terminal, probar
python test_local.py
```

## 🆚 Comparación: Vercel vs Railway

### Arquitectura

**Vercel (actual)**:
```
Chrome Extension → Vercel Serverless Function → Oxylabs
                    (10s timeout ⏱️)
```

**Railway (nuevo)**:
```
Chrome Extension → Railway Flask App → Oxylabs
                   (120s timeout ✅)
```

### Diferencias Técnicas

| Aspecto | Vercel | Railway |
|---------|--------|---------|
| Framework | BaseHTTPRequestHandler | Flask + Gunicorn |
| Timeout | 10s (Free), 60s (Pro) | 120s configurable |
| Cold Starts | ~1-2s | ~500ms |
| Logs | Por deploy | Real-time |
| Scaling | Automático | Manual vertical |
| Deploy | Git push | Git push |

## 📝 Cambios en el Código

### Lo que CAMBIÓ:

1. **Framework**: Vercel `BaseHTTPRequestHandler` → Railway `Flask`
2. **CORS**: Agregado `flask-cors` para Chrome Extension
3. **Timeout**: Oxylabs timeout: 8s → 60s
4. **Gunicorn**: Worker timeout configurado a 120s

### Lo que NO CAMBIÓ:

- ✅ Lógica de Oxylabs (igual)
- ✅ Lógica de Gemini (igual)
- ✅ Endpoints (`/health`, `/api/check_price`, `/api/debug`)
- ✅ Request/Response format (100% compatible con extension)
- ✅ Variables de entorno (mismos nombres)

## 🔄 Rollback Plan

Si algo sale mal con Railway, puedes volver a Vercel:

1. En la extension, vuelve a cambiar la URL a Vercel
2. Recarga la extensión
3. Todo funcionará como antes (con timeouts)

Vercel y Railway pueden coexistir. No necesitas borrar Vercel.

## ✅ Checklist de Migración

### Pre-deployment
- [x] Archivos Railway creados (`app.py`, `Procfile`, etc.)
- [ ] Variables de entorno documentadas
- [ ] Pruebas locales exitosas (opcional)

### Deployment
- [ ] Proyecto Railway creado
- [ ] Repo GitHub conectado
- [ ] Root directory configurado (`upc-backend-clean`)
- [ ] Variables de entorno configuradas
- [ ] Deploy exitoso
- [ ] Health check funciona
- [ ] Debug muestra variables SET
- [ ] Test de price check funciona (Oxylabs responde)

### Post-deployment
- [ ] URL de Railway copiada
- [ ] `background.js` actualizado con nueva URL
- [ ] Extensión recargada
- [ ] Prueba de captura de pantalla funciona
- [ ] Búsqueda de precios funciona
- [ ] Sin timeouts de Oxylabs

## 🐛 Troubleshooting

### Deploy falla

**Problema**: Railway no encuentra archivos
**Solución**: Verifica Root Directory = `upc-backend-clean`

### Variables no configuradas

**Problema**: `/api/debug` muestra "NOT SET"
**Solución**: Revisa Settings → Variables en Railway

### Oxylabs timeout persiste

**Problema**: Aún hay timeouts después de 120s
**Solución**:
1. Verifica credenciales Oxylabs en `/api/debug`
2. Revisa saldo de cuenta Oxylabs
3. Intenta con query más específico

### CORS errors

**Problema**: Extension muestra CORS error
**Solución**: Flask-CORS ya está configurado, recarga extension

## 📚 Recursos

- [Railway Docs](https://docs.railway.app/)
- [Flask Docs](https://flask.palletsprojects.com/)
- [Gunicorn Docs](https://docs.gunicorn.org/)
- [Oxylabs API Docs](https://developers.oxylabs.io/)

## 🎉 Próximos Pasos

Después de migrar a Railway:

1. **Monitorear costos** - Railway dashboard muestra uso en tiempo real
2. **Configurar alertas** - Railway puede enviar alerts si algo falla
3. **Custom domain** (opcional) - Puedes usar tu propio dominio
4. **Escalar** si es necesario - Aumentar RAM/CPU según tráfico

---

**¿Listo para migrar?** Sigue la guía detallada en `RAILWAY_DEPLOYMENT.md`

**Fecha**: Enero 22, 2026
