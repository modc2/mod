FROM node:18-alpine

WORKDIR /app

RUN apk add --no-cache python3 make g++ git

COPY package*.json ./

RUN npm install

COPY . .

RUN npm run compile

EXPOSE 8545

CMD ["npm", "run", "node"]
