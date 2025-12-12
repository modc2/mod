# Contributing to BlocTime

## Code Style

### Solidity
- Follow Solidity style guide
- Use 4 spaces for indentation
- Maximum line length: 120 characters
- Use NatSpec comments for all public functions
- Run `npm run lint:sol` before committing

### JavaScript
- Use ES6+ syntax
- Use 2 spaces for indentation
- Use single quotes for strings
- Run `npm run lint:js` before committing

## Testing

- Write tests for all new features
- Maintain 100% coverage for critical paths
- Run `npm test` before submitting PR
- Run `npm run coverage` to check coverage

## Commit Messages

Follow conventional commits:
- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation changes
- `test:` Test changes
- `refactor:` Code refactoring
- `chore:` Build/tooling changes

## Pull Request Process

1. Fork the repository
2. Create feature branch (`git checkout -b feat/amazing-feature`)
3. Commit changes (`git commit -m 'feat: add amazing feature'`)
4. Push to branch (`git push origin feat/amazing-feature`)
5. Open Pull Request

## Security

Report security issues to security@bloctime.io
