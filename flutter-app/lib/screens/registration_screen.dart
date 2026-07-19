import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:uuid/uuid.dart';
import '../config/theme.dart';
import '../services/api_service.dart';
import '../services/storage_service.dart';
import 'main_screen.dart';

class RegistrationScreen extends StatefulWidget {
  final VoidCallback onSkip;
  const RegistrationScreen({super.key, required this.onSkip});

  @override
  State<RegistrationScreen> createState() => _RegistrationScreenState();
}

class _RegistrationScreenState extends State<RegistrationScreen> {
  final _nameController = TextEditingController();
  final _passwordController = TextEditingController();
  final _vkController = TextEditingController();
  final _tgController = TextEditingController();
  final _api = ApiService();
  final _storage = StorageService();
  bool _isLoading = false;
  String? _error;
  bool _isLoginMode = false;
  bool _obscurePassword = true;

  @override
  void dispose() {
    _nameController.dispose();
    _passwordController.dispose();
    _vkController.dispose();
    _tgController.dispose();
    super.dispose();
  }

  Future<void> _register() async {
    final name = _nameController.text.trim();
    final password = _passwordController.text.trim();
    if (name.isEmpty) {
      setState(() => _error = 'Введите имя');
      return;
    }
    if (password.length < 4) {
      setState(() => _error = 'Пароль не менее 4 символов');
      return;
    }

    if (!mounted) return;
    setState(() {
      _isLoading = true;
      _error = null;
    });

    try {
      final deviceId = await _getDeviceId();
      final body = <String, dynamic>{
        'name': name,
        'password': password,
        'device_id': deviceId,
      };
      if (_vkController.text.trim().isNotEmpty) {
        body['vk_link'] = _vkController.text.trim();
      }
      if (_tgController.text.trim().isNotEmpty) {
        body['tg_link'] = _tgController.text.trim();
      }

      final resp = await _api.registerUser(body);

      if (!mounted) return;
      if (resp['ok'] == true && resp['user_id'] != null) {
        final userId = resp['user_id'] as int;
        final telegramId = resp['telegram_id'] as int? ?? userId;
        _storage.userId = userId;
        _storage.telegramId = telegramId;
        _api.setUserId(telegramId);

        if (mounted) {
          Navigator.of(context).pushReplacement(
            MaterialPageRoute(builder: (_) => const MainScreen()),
          );
        }
      } else {
        setState(() {
          _error = resp['error']?.toString() ?? 'Ошибка регистрации';
          _isLoading = false;
        });
      }
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = 'Ошибка сети. Попробуй ещё раз.';
        _isLoading = false;
      });
    }
  }

  Future<void> _login() async {
    final name = _nameController.text.trim();
    final password = _passwordController.text.trim();
    if (name.isEmpty || password.isEmpty) {
      setState(() => _error = 'Введите имя и пароль');
      return;
    }

    if (!mounted) return;
    setState(() {
      _isLoading = true;
      _error = null;
    });

    try {
      final resp = await _api.loginUser(name, password);

      if (!mounted) return;
      if (resp['ok'] == true && resp['user_id'] != null) {
        final userId = resp['user_id'] as int;
        final telegramId = resp['telegram_id'] as int? ?? userId;
        _storage.userId = userId;
        _storage.telegramId = telegramId;
        _api.setUserId(telegramId);

        if (mounted) {
          Navigator.of(context).pushReplacement(
            MaterialPageRoute(builder: (_) => const MainScreen()),
          );
        }
      } else {
        setState(() {
          _error = resp['error']?.toString() ?? 'Ошибка входа';
          _isLoading = false;
        });
      }
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = 'Ошибка сети. Попробуй ещё раз.';
        _isLoading = false;
      });
    }
  }

  void _skip() {
    widget.onSkip();
  }

  Future<String> _getDeviceId() async {
    final prefs = await SharedPreferences.getInstance();
    var id = prefs.getString('device_id');
    if (id == null) {
      id = const Uuid().v4();
      await prefs.setString('device_id', id);
    }
    return id;
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Container(
        width: double.infinity,
        height: double.infinity,
        decoration: const BoxDecoration(
          gradient: LinearGradient(
            begin: Alignment.topCenter,
            end: Alignment.bottomCenter,
            colors: [Color(0xFF0a0e1a), Color(0xFF0f1629)],
          ),
        ),
        child: SafeArea(
          child: SingleChildScrollView(
            padding: const EdgeInsets.symmetric(horizontal: 24),
            child: Column(
              children: [
                const SizedBox(height: 60),
                _buildLogo(),
                const SizedBox(height: 32),
                Text(
                  _isLoginMode ? 'Вход в аккаунт' : 'Добро пожаловать',
                  style: const TextStyle(
                    color: AppTheme.textPrimary,
                    fontSize: 26,
                    fontWeight: FontWeight.w800,
                  ),
                ),
                const SizedBox(height: 8),
                Text(
                  _isLoginMode ? 'Введи имя и пароль' : 'Как тебя зовут?',
                  style: const TextStyle(
                    color: AppTheme.textSecondary,
                    fontSize: 15,
                  ),
                ),
                const SizedBox(height: 32),
                _buildNameField(),
                const SizedBox(height: 12),
                _buildPasswordField(),
                if (!_isLoginMode) ...[
                  const SizedBox(height: 12),
                  _buildVkField(),
                  const SizedBox(height: 12),
                  _buildTgField(),
                ],
                if (_error != null) ...[
                  const SizedBox(height: 12),
                  _buildError(),
                ],
                const SizedBox(height: 24),
                _buildMainButton(),
                const SizedBox(height: 12),
                _buildToggleMode(),
                const SizedBox(height: 12),
                _buildSkipButton(),
                const SizedBox(height: 40),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildLogo() {
    return Container(
      width: 80,
      height: 80,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        gradient: const LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [Color(0xFFff6b35), Color(0xFFf7931e)],
        ),
        boxShadow: [
          BoxShadow(
            color: const Color(0xFFff6b35).withValues(alpha: 0.3),
            blurRadius: 24,
            spreadRadius: 4,
          ),
        ],
      ),
      child: const Center(
        child: Text('⛽', style: TextStyle(fontSize: 40)),
      ),
    );
  }

  Widget _buildNameField() {
    return TextField(
      controller: _nameController,
      style: const TextStyle(color: AppTheme.textPrimary),
      textCapitalization: TextCapitalization.words,
      decoration: const InputDecoration(
        hintText: 'Имя *',
        prefixIcon: Icon(Icons.person_outline, color: AppTheme.muted),
      ),
      onSubmitted: (_) => _register(),
    );
  }

  Widget _buildVkField() {
    return TextField(
      controller: _vkController,
      style: const TextStyle(color: AppTheme.textPrimary),
      decoration: const InputDecoration(
        hintText: 'Ссылка VK (необязательно)',
        prefixIcon: Icon(Icons.videocam_outlined, color: AppTheme.muted),
      ),
    );
  }

  Widget _buildTgField() {
    return TextField(
      controller: _tgController,
      style: const TextStyle(color: AppTheme.textPrimary),
      decoration: const InputDecoration(
        hintText: 'Ссылка Telegram (необязательно)',
        prefixIcon: Icon(Icons.telegram, color: AppTheme.muted),
      ),
    );
  }

  Widget _buildPasswordField() {
    return TextField(
      controller: _passwordController,
      obscureText: _obscurePassword,
      style: const TextStyle(color: AppTheme.textPrimary),
      decoration: InputDecoration(
        hintText: 'Пароль *',
        prefixIcon: const Icon(Icons.lock_outline, color: AppTheme.muted),
        suffixIcon: IconButton(
          icon: Icon(
            _obscurePassword ? Icons.visibility_off : Icons.visibility,
            color: AppTheme.muted,
            size: 20,
          ),
          onPressed: () => setState(() => _obscurePassword = !_obscurePassword),
        ),
      ),
    );
  }

  Widget _buildError() {
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: AppTheme.danger.withValues(alpha: 0.1),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Row(
        children: [
          const Icon(Icons.error_outline, color: AppTheme.danger, size: 18),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              _error!,
              style: const TextStyle(color: AppTheme.danger, fontSize: 13),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildMainButton() {
    return SizedBox(
      width: double.infinity,
      child: ElevatedButton(
        onPressed: _isLoading ? null : (_isLoginMode ? _login : _register),
        child: _isLoading
            ? const SizedBox(
                width: 20,
                height: 20,
                child: CircularProgressIndicator(
                    strokeWidth: 2, color: Colors.white))
            : Text(_isLoginMode ? 'Войти' : 'Создать аккаунт'),
      ),
    );
  }

  Widget _buildToggleMode() {
    return TextButton(
      onPressed: () => setState(() {
        _isLoginMode = !_isLoginMode;
        _error = null;
      }),
      child: Text(
        _isLoginMode
            ? 'Нет аккаунта? Зарегистрируйся'
            : 'Уже есть аккаунт? Войти',
        style: const TextStyle(color: AppTheme.accent, fontSize: 14),
      ),
    );
  }

  Widget _buildSkipButton() {
    return TextButton(
      onPressed: _skip,
      child: const Text(
        'Пропустить',
        style: TextStyle(color: AppTheme.muted, fontSize: 14),
      ),
    );
  }
}
