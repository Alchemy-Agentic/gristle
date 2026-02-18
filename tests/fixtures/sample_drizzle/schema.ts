import { pgTable, varchar, uuid, timestamp, boolean, text, index } from 'drizzle-orm/pg-core';

export const users = pgTable('users', {
  id: uuid('id').primaryKey().defaultRandom(),
  email: varchar('email', { length: 255 }).notNull().unique(),
  name: varchar('name', { length: 255 }),
  createdAt: timestamp('created_at').defaultNow(),
});

export const posts = pgTable('posts', {
  id: uuid('id').primaryKey().defaultRandom(),
  title: varchar('title', { length: 255 }).notNull(),
  content: text('content'),
  published: boolean('published').default(false),
  authorId: uuid('author_id').notNull().references(() => users.id),
  createdAt: timestamp('created_at').defaultNow(),
}, (table) => ({
  authorIdx: index('author_idx').on(table.authorId),
}));
