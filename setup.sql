-- ============================================
-- Upscale Bot Database Schema for Supabase
-- Run this SQL in your Supabase SQL Editor
-- ============================================

-- Enable UUID extension (if not already enabled)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================
-- Users Table
-- ============================================
CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    discord_user_id TEXT UNIQUE NOT NULL,
    credits INTEGER DEFAULT 10,
    total_credits_used INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create index for faster lookups
CREATE INDEX IF NOT EXISTS idx_users_discord_id ON users(discord_user_id);

-- ============================================
-- Requests Table
-- ============================================
CREATE TABLE IF NOT EXISTS requests (
    id BIGSERIAL PRIMARY KEY,
    discord_user_id TEXT NOT NULL,
    folder_url TEXT NOT NULL,
    channel_id TEXT,
    status TEXT DEFAULT 'pending',
    pixel_count BIGINT,
    output_url TEXT,
    error_message TEXT,
    credits_deducted INTEGER,
    notified BOOLEAN DEFAULT FALSE,
    gdrive_file_cleaned BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    FOREIGN KEY (discord_user_id) REFERENCES users(discord_user_id) ON DELETE CASCADE
);

-- Create indexes for faster queries
CREATE INDEX IF NOT EXISTS idx_requests_discord_id ON requests(discord_user_id);
CREATE INDEX IF NOT EXISTS idx_requests_status ON requests(status);
CREATE INDEX IF NOT EXISTS idx_requests_created_at ON requests(created_at);

-- ============================================
-- Transaction Log Table
-- ============================================
CREATE TABLE IF NOT EXISTS transaction_log (
    id BIGSERIAL PRIMARY KEY,
    discord_user_id TEXT NOT NULL,
    transaction_type TEXT NOT NULL,
    amount INTEGER NOT NULL,
    balance_before INTEGER NOT NULL,
    balance_after INTEGER NOT NULL,
    description TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    FOREIGN KEY (discord_user_id) REFERENCES users(discord_user_id) ON DELETE CASCADE
);

-- Create indexes for faster queries
CREATE INDEX IF NOT EXISTS idx_transactions_discord_id ON transaction_log(discord_user_id);
CREATE INDEX IF NOT EXISTS idx_transactions_type ON transaction_log(transaction_type);
CREATE INDEX IF NOT EXISTS idx_transactions_created_at ON transaction_log(created_at);

-- ============================================
-- Settings Table
-- ============================================
CREATE TABLE IF NOT EXISTS settings (
    id BIGSERIAL PRIMARY KEY,
    key TEXT UNIQUE NOT NULL,
    value TEXT NOT NULL,
    description TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create index for faster lookups
CREATE INDEX IF NOT EXISTS idx_settings_key ON settings(key);

-- ============================================
-- Functions and Triggers
-- ============================================

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Trigger for users table
CREATE TRIGGER update_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Trigger for requests table
CREATE TRIGGER update_requests_updated_at
    BEFORE UPDATE ON requests
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Trigger for settings table
CREATE TRIGGER update_settings_updated_at
    BEFORE UPDATE ON settings
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ============================================
-- Row Level Security (RLS) Policies
-- ============================================

-- Enable RLS on all tables
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE transaction_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE settings ENABLE ROW LEVEL SECURITY;

-- Allow service role to do everything
CREATE POLICY "Service role can do everything" ON users
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

CREATE POLICY "Service role can do everything" ON requests
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

CREATE POLICY "Service role can do everything" ON transaction_log
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

CREATE POLICY "Service role can do everything" ON settings
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- ============================================
-- Insert default settings
-- ============================================
INSERT INTO settings (key, value, description) VALUES
    ('default_credits', '10', 'Default credits for new users'),
    ('credits_per_100k_pixels', '1', 'Credits needed per 100k pixels'),
    ('max_credits_per_request', '50', 'Maximum credits that can be deducted per request'),
    ('upscale_model', 'RealESRGAN_x4plus_anime_6B', 'Default upscaling model'),
    ('upscale_scale', '4', 'Default upscale factor'),
    ('compression_quality', '95', 'Output image quality (1-100)'),
    ('max_image_size_mb', '50', 'Maximum image file size in MB'),
    ('allow_zip_upload', 'true', 'Allow ZIP/RAR uploads'),
    ('auto_create_user', 'true', 'Auto-create user on first use'),
    ('estimated_processing_time_per_chapter_seconds', '120', 'Estimated processing time per chapter in seconds')
ON CONFLICT (key) DO NOTHING;

-- ============================================
-- Grant Privileges to Roles
-- ============================================

-- إعطاء صلاحيات كاملة للمفتاح الخارق (Service Role) على الجداول الحالية
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO service_role;

-- إعطاء صلاحيات للمفتاح الخارق على العدادات (Sequences) عشان يقدر يضيف أسطر جديدة بأرقام تسلسلية (مثل ID)
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO service_role;

-- إعطاء نفس الصلاحيات تلقائياً لأي جدول أو عداد يتم إنشاؤه مستقبلاً داخل هذا المخطط
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO service_role;

-- ============================================
-- Insert test user (optional)
-- ============================================
INSERT INTO users (discord_user_id, credits, total_credits_used) VALUES
    ('123456789012345678', 1000, 0)
ON CONFLICT (discord_user_id) DO NOTHING;

-- ============================================
-- Verification Queries
-- ============================================

-- Check tables were created
SELECT table_name 
FROM information_schema.tables 
WHERE table_schema = 'public' 
AND table_name IN ('users', 'requests', 'transaction_log', 'settings');

-- Check indexes were created
SELECT indexname, tablename 
FROM pg_indexes 
WHERE tablename IN ('users', 'requests', 'transaction_log', 'settings');

-- Check RLS policies
SELECT schemaname, tablename, policyname 
FROM pg_policies 
WHERE tablename IN ('users', 'requests', 'transaction_log', 'settings');