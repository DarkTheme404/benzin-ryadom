import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';
import '../config/theme.dart';
import '../models/user.dart';
import '../services/api_service.dart';
import '../widgets/premium_card.dart';
import '../widgets/referral_card.dart';
import 'premium_screen.dart';
import 'settings_screen.dart';

class ProfileScreen extends StatefulWidget {
  const ProfileScreen({super.key});

  @override
  State<ProfileScreen> createState() => _ProfileScreenState();
}

class _ProfileScreenState extends State<ProfileScreen> {
  final ApiService _api = ApiService();
  UserProfile? _profile;
  bool _isLoading = true;

  @override
  void initState() {
    super.initState();
    _loadProfile();
  }

  Future<void> _loadProfile() async {
    try {
      final profile = await _api.getUserProfile();
      setState(() {
        _profile = profile;
        _isLoading = false;
      });
    } catch (_) {
      setState(() => _isLoading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Профиль'),
        actions: [
          IconButton(
            icon: const Icon(Icons.settings_outlined),
            onPressed: () => Navigator.push(
              context,
              MaterialPageRoute(builder: (_) => const SettingsScreen()),
            ),
          ),
        ],
      ),
      body: _isLoading
          ? const Center(
              child: CircularProgressIndicator(color: AppTheme.accent))
          : RefreshIndicator(
              onRefresh: _loadProfile,
              color: AppTheme.accent,
              child: SingleChildScrollView(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    _buildUserHeader(),
                    const SizedBox(height: 16),
                    PremiumCard(
                      tier: _profile?.premium,
                      isFounder: _profile?.isFounder ?? false,
                      expires: _profile?.premiumExpires,
                      onTap: () => Navigator.push(
                        context,
                        MaterialPageRoute(
                            builder: (_) => const PremiumScreen()),
                      ),
                    ),
                    const SizedBox(height: 16),
                    _buildStatsCard(),
                    const SizedBox(height: 16),
                    ReferralCard(userId: _profile?.id),
                    const SizedBox(height: 16),
                    _buildLinksCard(),
                    const SizedBox(height: 16),
                    _buildAboutCard(),
                  ],
                ),
              ),
            ),
    );
  }

  Widget _buildUserHeader() {
    return Container(
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        gradient: const LinearGradient(
          colors: [AppTheme.bgCard, AppTheme.bgCardLight],
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
        ),
        borderRadius: BorderRadius.circular(16),
      ),
      child: Row(
        children: [
          CircleAvatar(
            radius: 32,
            backgroundColor: AppTheme.accent.withValues(alpha: 0.2),
            child: Text(
              _profile?.displayName.substring(0, 1).toUpperCase() ?? '?',
              style: const TextStyle(
                color: AppTheme.accent,
                fontSize: 24,
                fontWeight: FontWeight.w700,
              ),
            ),
          ),
          const SizedBox(width: 16),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  _profile?.displayName ?? 'Пользователь',
                  style: const TextStyle(
                    color: AppTheme.textPrimary,
                    fontSize: 18,
                    fontWeight: FontWeight.w700,
                  ),
                ),
                const SizedBox(height: 4),
                if (_profile?.isFounder == true)
                  Row(
                    children: [
                      const Icon(Icons.star, color: AppTheme.premium, size: 14),
                      const SizedBox(width: 4),
                      Text(
                        'Founder',
                        style: TextStyle(
                          color: AppTheme.premium,
                          fontSize: 13,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                    ],
                  )
                else if (_profile?.hasPremium == true)
                  Text(
                    _premiumTierName(_profile!.premium!),
                    style: const TextStyle(
                      color: AppTheme.accent,
                      fontSize: 13,
                      fontWeight: FontWeight.w600,
                    ),
                  )
                else
                  const Text(
                    'Free',
                    style: TextStyle(
                      color: AppTheme.muted,
                      fontSize: 13,
                    ),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildStatsCard() {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppTheme.bgCard,
        borderRadius: BorderRadius.circular(16),
      ),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceAround,
        children: [
          _buildStatItem(
            Icons.assessment,
            _profile?.reportCount.toString() ?? '0',
            'Отчётов',
          ),
          _buildStatItem(
            Icons.savings_outlined,
            '${_profile?.savings.round() ?? 0}₽',
            'Экономия',
          ),
          _buildStatItem(
            Icons.star_outline,
            _profile?.isFounder == true ? '∞' : _premiumDaysLeft(),
            'Дней',
          ),
        ],
      ),
    );
  }

  Widget _buildStatItem(IconData icon, String value, String label) {
    return Column(
      children: [
        Icon(icon, color: AppTheme.accent, size: 24),
        const SizedBox(height: 8),
        Text(
          value,
          style: const TextStyle(
            color: AppTheme.textPrimary,
            fontSize: 18,
            fontWeight: FontWeight.w700,
          ),
        ),
        const SizedBox(height: 4),
        Text(
          label,
          style: const TextStyle(color: AppTheme.muted, fontSize: 12),
        ),
      ],
    );
  }

  Widget _buildLinksCard() {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppTheme.bgCard,
        borderRadius: BorderRadius.circular(16),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text('Наши каналы',
              style: TextStyle(
                color: AppTheme.textPrimary,
                fontSize: 16,
                fontWeight: FontWeight.w600,
              )),
          const SizedBox(height: 12),
          _buildLinkRow(
            Icons.telegram,
            'Telegram бот',
            'https://t.me/benzyn_ryadom_bot',
          ),
          _buildLinkRow(
            Icons.telegram,
            'Telegram канал',
            'https://t.me/benzyn_ryadom',
          ),
          _buildLinkRow(
            Icons.videocam,
            'ВКонтакте',
            'https://vk.com/benzyn_ryadom',
          ),
        ],
      ),
    );
  }

  Widget _buildLinkRow(IconData icon, String title, String url) {
    return ListTile(
      contentPadding: EdgeInsets.zero,
      leading: Icon(icon, color: AppTheme.info, size: 20),
      title: Text(title,
          style: const TextStyle(color: AppTheme.textPrimary, fontSize: 14)),
      trailing:
          const Icon(Icons.open_in_new, color: AppTheme.muted, size: 16),
      onTap: () => launchUrl(Uri.parse(url)),
    );
  }

  Widget _buildAboutCard() {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppTheme.bgCard,
        borderRadius: BorderRadius.circular(16),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text('О приложении',
              style: TextStyle(
                color: AppTheme.textPrimary,
                fontSize: 16,
                fontWeight: FontWeight.w600,
              )),
          const SizedBox(height: 8),
          Text(
            'Бензин рядом — найди бензин за 5 секунд.\n'
            '26 000+ АЗС по всей России.\n'
            'v1.0.0',
            style: TextStyle(
              color: AppTheme.muted,
              fontSize: 13,
              height: 1.5,
            ),
          ),
        ],
      ),
    );
  }

  String _premiumTierName(String tier) {
    switch (tier) {
      case 'economy':
        return 'Economy';
      case 'standard':
        return 'Standard';
      case 'elite':
        return 'Elite';
      case 'founder':
        return 'Founder';
      default:
        return tier;
    }
  }

  String _premiumDaysLeft() {
    if (_profile?.premiumExpires == null) return '—';
    final diff = _profile!.premiumExpires!.difference(DateTime.now()).inDays;
    return diff > 0 ? diff.toString() : '0';
  }
}
