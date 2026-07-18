import 'package:flutter/material.dart';
import '../config/theme.dart';
import '../services/storage_service.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  final StorageService _storage = StorageService();

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Настройки')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          _buildSection('Город', [
            _buildCitySelector(),
          ]),
          const SizedBox(height: 16),
          _buildSection('Топливо по умолчанию', [
            _buildFuelSelector(),
          ]),
          const SizedBox(height: 16),
          _buildSection('Прочее', [
            _buildInfoRow('Версия', '1.0.0'),
            _buildInfoRow('Платформа', 'Android'),
          ]),
        ],
      ),
    );
  }

  Widget _buildSection(String title, List<Widget> children) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppTheme.bgCard,
        borderRadius: BorderRadius.circular(16),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title,
              style: const TextStyle(
                color: AppTheme.textPrimary,
                fontSize: 16,
                fontWeight: FontWeight.w600,
              )),
          const SizedBox(height: 12),
          ...children,
        ],
      ),
    );
  }

  Widget _buildCitySelector() {
    return DropdownButton<String>(
      value: _storage.selectedCity ?? 'Иваново',
      isExpanded: true,
      dropdownColor: AppTheme.bgCard,
      underline: const SizedBox(),
      style: const TextStyle(color: AppTheme.textPrimary, fontSize: 14),
      items: const [
        DropdownMenuItem(value: 'Иваново', child: Text('Иваново')),
        DropdownMenuItem(value: 'Москва', child: Text('Москва')),
        DropdownMenuItem(value: 'Санкт-Петербург', child: Text('Санкт-Петербург')),
        DropdownMenuItem(value: 'Владимир', child: Text('Владимир')),
        DropdownMenuItem(value: 'Кострома', child: Text('Кострома')),
        DropdownMenuItem(value: 'Ярославль', child: Text('Ярославль')),
        DropdownMenuItem(value: 'Тверь', child: Text('Тверь')),
        DropdownMenuItem(value: 'Нижний Новгород', child: Text('Нижний Новгород')),
      ],
      onChanged: (v) {
        if (v != null) {
          setState(() => _storage.selectedCity = v);
        }
      },
    );
  }

  Widget _buildFuelSelector() {
    return DropdownButton<String>(
      value: _storage.selectedFuel ?? '95',
      isExpanded: true,
      dropdownColor: AppTheme.bgCard,
      underline: const SizedBox(),
      style: const TextStyle(color: AppTheme.textPrimary, fontSize: 14),
      items: const [
        DropdownMenuItem(value: '92', child: Text('АИ-92')),
        DropdownMenuItem(value: '95', child: Text('АИ-95')),
        DropdownMenuItem(value: '98', child: Text('АИ-98')),
        DropdownMenuItem(value: 'diesel', child: Text('Дизель')),
        DropdownMenuItem(value: 'lpg', child: Text('Газ (LPG)')),
      ],
      onChanged: (v) {
        if (v != null) {
          setState(() => _storage.selectedFuel = v);
        }
      },
    );
  }

  Widget _buildInfoRow(String label, String value) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Text(label,
              style: const TextStyle(color: AppTheme.muted, fontSize: 14)),
          Text(value,
              style: const TextStyle(
                  color: AppTheme.textSecondary, fontSize: 14)),
        ],
      ),
    );
  }
}
