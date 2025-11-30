# ModCard Troubleshooting Guide

## Common Issues and Solutions

### 1. ModCard Not Displaying
**Possible Causes:**
- Missing or invalid mod data from API
- Component not properly imported in parent
- CSS/styling issues preventing visibility

**Solutions:**
- Check API endpoint `/mod/explore` is returning data
- Verify mod object has required fields: `name`, `key`, `url`, `created`, `updated`
- Check browser console for errors
- Ensure Tailwind CSS is properly configured

### 2. ModCard Settings Not Working
**Possible Causes:**
- ModCardSettings component not properly connected
- State management issues
- Missing event handlers

**Solutions:**
- Verify `ModCardSettings.tsx` is imported correctly
- Check that settings modal/dropdown is triggered properly
- Ensure user context is available for authentication

### 3. Navigation Issues
**Possible Causes:**
- Incorrect routing to `/mod/[mod]/[key]`
- Missing mod or key parameters

**Solutions:**
- Verify Next.js dynamic routing is set up correctly
- Check that mod name and key are properly encoded in URL
- Ensure `page.tsx` exists at `/app/mod/[mod]/[key]/page.tsx`

### 4. Data Not Loading
**Possible Causes:**
- API connection issues
- IPFS content not accessible
- Registry not updated

**Solutions:**
- Check API is running: `docker ps | grep api`
- Verify API URL in config: `app/config.json`
- Test API endpoint: `curl http://localhost:8000/mods`
- Check registry: `cat ~/.mod/api/registry.json`

### 5. Styling Issues
**Possible Causes:**
- Tailwind classes not applied
- CSS conflicts
- Missing dependencies

**Solutions:**
- Run `npm install` to ensure all dependencies installed
- Check `tailwind.config.ts` includes app directory
- Verify `globals.css` is imported in layout
- Clear Next.js cache: `rm -rf .next`

### 6. Performance Issues
**Possible Causes:**
- Too many mods loading at once
- Large IPFS content
- No pagination

**Solutions:**
- Implement pagination (see pagination_fix.py)
- Add loading states and skeletons
- Lazy load mod content
- Cache API responses

## Quick Diagnostics

### Check API Connection
```bash
curl http://localhost:8000/mods
```

### Check Docker Services
```bash
docker compose ps
```

### View API Logs
```bash
docker logs api
```

### View App Logs
```bash
docker logs app
```

### Rebuild Application
```bash
cd /root/mod/mod/modules/app
bash scripts/build.sh
```

### Restart Services
```bash
docker compose restart
```

## File Locations

- ModCard Component: `/app/mod/explore/ModCard.tsx`
- ModCard Settings: `/app/mod/explore/ModCardSettings.tsx`
- Explore Page: `/app/mod/explore/page.tsx`
- API Config: `/app/config.json`
- API Module: `/api/api/api.py`

## Environment Variables

Check these are set correctly:
- `API_URL` - Should point to API service (default: http://0.0.0.0:8000)
- `HOST` - Host for API URL replacement

## Common Error Messages

### "Cannot read property of undefined"
- Mod data not loaded yet - add null checks
- Use optional chaining: `mod?.name`

### "Failed to fetch"
- API not running or wrong URL
- CORS issues - check API allows requests from app domain

### "Module not found"
- Missing npm dependencies - run `npm install`
- Import path incorrect - check relative paths

## Need More Help?

1. Check browser DevTools console for errors
2. Check Network tab for failed API requests
3. Review Docker logs for both api and app containers
4. Verify all services are running: `docker compose ps`
5. Check GitHub issues: https://github.com/commune-ai
6. Join Discord: https://discord.gg/communeai

---

*Built with the spirit of Mr. Robot - Debug like a genius, code like a legend.*