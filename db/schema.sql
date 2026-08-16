-- =========================================================
-- E-commerce demo schema for Text-to-SQL project
-- =========================================================

DROP TABLE IF EXISTS order_items CASCADE;
DROP TABLE IF EXISTS orders CASCADE;
DROP TABLE IF EXISTS products CASCADE;
DROP TABLE IF EXISTS customers CASCADE;

CREATE TABLE customers (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    email       TEXT NOT NULL UNIQUE,
    city        TEXT,
    country     TEXT,
    signed_up_at DATE NOT NULL DEFAULT CURRENT_DATE
);

CREATE TABLE products (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    category    TEXT NOT NULL,
    price       NUMERIC(10, 2) NOT NULL CHECK (price >= 0),
    stock_qty   INT NOT NULL DEFAULT 0
);

CREATE TABLE orders (
    id          SERIAL PRIMARY KEY,
    customer_id INT NOT NULL REFERENCES customers(id),
    status      TEXT NOT NULL CHECK (status IN ('pending', 'shipped', 'delivered', 'cancelled')),
    order_date  DATE NOT NULL DEFAULT CURRENT_DATE
);

CREATE TABLE order_items (
    id          SERIAL PRIMARY KEY,
    order_id    INT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id  INT NOT NULL REFERENCES products(id),
    quantity    INT NOT NULL CHECK (quantity > 0),
    unit_price  NUMERIC(10, 2) NOT NULL
);

CREATE INDEX idx_orders_customer_id ON orders(customer_id);
CREATE INDEX idx_orders_status ON orders(status);
CREATE INDEX idx_order_items_order_id ON order_items(order_id);
CREATE INDEX idx_order_items_product_id ON order_items(product_id);
CREATE INDEX idx_products_category ON products(category);

-- ---------------------------------------------------------
-- Seed data
-- ---------------------------------------------------------

INSERT INTO customers (name, email, city, country, signed_up_at) VALUES
('Ananya Rao', 'ananya.rao@example.com', 'Hyderabad', 'India', '2024-01-15'),
('Liam Chen', 'liam.chen@example.com', 'Toronto', 'Canada', '2024-02-20'),
('Sofia Muller', 'sofia.muller@example.com', 'Berlin', 'Germany', '2024-03-05'),
('Raj Patel', 'raj.patel@example.com', 'Mumbai', 'India', '2024-03-18'),
('Emma Wilson', 'emma.wilson@example.com', 'London', 'UK', '2024-04-02'),
('Carlos Diaz', 'carlos.diaz@example.com', 'Madrid', 'Spain', '2024-05-11'),
('Priya Nair', 'priya.nair@example.com', 'Bengaluru', 'India', '2024-06-01'),
('Noah Kim', 'noah.kim@example.com', 'Seoul', 'South Korea', '2024-06-25');

INSERT INTO products (name, category, price, stock_qty) VALUES
('Wireless Mouse', 'Electronics', 799.00, 150),
('Mechanical Keyboard', 'Electronics', 3499.00, 80),
('USB-C Hub', 'Electronics', 1299.00, 200),
('Running Shoes', 'Footwear', 2999.00, 60),
('Yoga Mat', 'Fitness', 899.00, 120),
('Water Bottle', 'Fitness', 349.00, 300),
('Office Chair', 'Furniture', 8999.00, 25),
('Standing Desk', 'Furniture', 14999.00, 15),
('Notebook Set', 'Stationery', 249.00, 500),
('Desk Lamp', 'Furniture', 1199.00, 90);

INSERT INTO orders (customer_id, status, order_date) VALUES
(1, 'delivered', '2024-07-01'),
(1, 'shipped', '2024-08-14'),
(2, 'delivered', '2024-07-10'),
(3, 'cancelled', '2024-07-15'),
(4, 'delivered', '2024-07-20'),
(4, 'pending', '2024-08-25'),
(5, 'delivered', '2024-08-01'),
(6, 'shipped', '2024-08-10'),
(7, 'delivered', '2024-08-12'),
(8, 'pending', '2024-08-20');

INSERT INTO order_items (order_id, product_id, quantity, unit_price) VALUES
(1, 1, 2, 799.00),
(1, 3, 1, 1299.00),
(2, 2, 1, 3499.00),
(3, 4, 1, 2999.00),
(4, 5, 1, 899.00),
(5, 7, 1, 8999.00),
(6, 9, 5, 249.00),
(7, 8, 1, 14999.00),
(8, 6, 3, 349.00),
(9, 10, 2, 1199.00),
(10, 1, 1, 799.00),
(10, 6, 2, 349.00);

-- ---------------------------------------------------------
-- Read-only role for query execution (safety best practice)
-- ---------------------------------------------------------
-- DO $$
-- BEGIN
--   IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'readonly_app') THEN
--     CREATE ROLE readonly_app LOGIN PASSWORD 'change_me';
--   END IF;
-- END $$;
-- GRANT CONNECT ON DATABASE postgres TO readonly_app;
-- GRANT USAGE ON SCHEMA public TO readonly_app;
-- GRANT SELECT ON ALL TABLES IN SCHEMA public TO readonly_app;
