pgx_fhir_server        | 2025-04-05T21:30:08.259Z ERROR 1 --- [           main] irLocalContainerEntityManagerFactoryBean : Failed to initialize JPA EntityManagerFactory: Unable to create requested service [org.hibernate.engine.jdbc.env.spi.JdbcEnvironment] due to: Unable to resolve name [ca.uhn.fhir.jpa.model.dialect.HapiFhirPostgres95Dialect] as strategy [org.hibernate.dialect.Dialect]
pgx_fhir_server        | 2025-04-05T21:30:08.260Z ERROR 1 --- [           main] o.s.b.web.embedded.tomcat.TomcatStarter  : Error starting Tomcat context. Exception: org.springframework.beans.factory.UnsatisfiedDependencyException. Message: Error creating bean with name 'hapiServletRegistration' defined in ca.uhn.fhir.jpa.starter.Application: Unsatisfied dependency expressed through method 'hapiServletRegistration' parameter 0: Error creating bean with name 'restfulServer' defined in class path resource [ca/uhn/fhir/jpa/starter/common/StarterJpaConfig.class]: Unsatisfied dependency expressed through method 'restfulServer' parameter 0: Error creating bean with name 'mySystemDaoR4': Injection of persistence dependencies failed
pgx_fhir_server        | 2025-04-05T21:30:08.289Z  INFO 1 --- [           main] o.apache.catalina.core.StandardService   : Stopping service [Tomcat]
pgx_fhir_server        | 2025-04-05T21:30:08.294Z  WARN 1 --- [           main] o.a.c.loader.WebappClassLoaderBase       : The web application [ROOT] appears to have started a thread named [HikariPool-1 housekeeper] but has failed to stop it. This is very likely to create a memory leak. Stack trace of thread:
pgx_fhir_server        |  java.base@17.0.14/jdk.internal.misc.Unsafe.park(Native Method)
pgx_fhir_server        |  java.base@17.0.14/java.util.concurrent.locks.LockSupport.parkNanos(LockSupport.java:252)
pgx_fhir_server        |  java.base@17.0.14/java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionObject.awaitNanos(AbstractQueuedSynchronizer.java:1679)
pgx_fhir_server        |  java.base@17.0.14/java.util.concurrent.ScheduledThreadPoolExecutor$DelayedWorkQueue.take(ScheduledThreadPoolExecutor.java:1182)
pgx_fhir_server        |  java.base@17.0.14/java.util.concurrent.ScheduledThreadPoolExecutor$DelayedWorkQueue.take(ScheduledThreadPoolExecutor.java:899)
pgx_fhir_server        |  java.base@17.0.14/java.util.concurrent.ThreadPoolExecutor.getTask(ThreadPoolExecutor.java:1062)
pgx_fhir_server        |  java.base@17.0.14/java.util.concurrent.ThreadPoolExecutor.runWorker(ThreadPoolExecutor.java:1122)
pgx_fhir_server        |  java.base@17.0.14/java.util.concurrent.ThreadPoolExecutor$Worker.run(ThreadPoolExecutor.java:635)
pgx_fhir_server        |  java.base@17.0.14/java.lang.Thread.run(Thread.java:840)
pgx_fhir_server        | 2025-04-05T21:30:08.296Z  WARN 1 --- [           main] ConfigServletWebServerApplicationContext : Exception encountered during context initialization - cancelling refresh attempt: org.springframework.context.ApplicationContextException: Unable to start web server
pgx_fhir_server        | 2025-04-05T21:30:08.296Z  INFO 1 --- [           main] com.zaxxer.hikari.HikariDataSource       : HikariPool-1 - Shutdown initiated...
pgx_fhir_server        | 2025-04-05T21:30:08.298Z  INFO 1 --- [           main] com.zaxxer.hikari.HikariDataSource       : HikariPool-1 - Shutdown completed.
pgx_fhir_server        | 2025-04-05T21:30:08.314Z  INFO 1 --- [           main] .s.b.a.l.ConditionEvaluationReportLogger :
pgx_fhir_server        |
pgx_fhir_server        | Error starting ApplicationContext. To display the condition evaluation report re-run your application with 'debug' enabled.
pgx_fhir_server        | 2025-04-05T21:30:08.346Z ERROR 1 --- [           main] o.s.boot.SpringApplication               : Application run failed
pgx_fhir_server        |
pgx_fhir_server        | org.springframework.context.ApplicationContextException: Unable to start web server
pgx_fhir_server        |        at org.springframework.boot.web.servlet.context.ServletWebServerApplicationContext.onRefresh(ServletWebServerApplicationContext.java:165)
pgx_fhir_server        |        at org.springframework.context.support.AbstractApplicationContext.refresh(AbstractApplicationContext.java:619)
pgx_fhir_server        |        at org.springframework.boot.web.servlet.context.ServletWebServerApplicationContext.refresh(ServletWebServerApplicationContext.java:146)
pgx_fhir_server        |        at org.springframework.boot.SpringApplication.refresh(SpringApplication.java:754)
pgx_fhir_server        |        at org.springframework.boot.SpringApplication.refreshContext(SpringApplication.java:456)
pgx_fhir_server        |        at org.springframework.boot.SpringApplication.run(SpringApplication.java:335)
pgx_fhir_server        |        at org.springframework.boot.SpringApplication.run(SpringApplication.java:1363)
pgx_fhir_server        |        at org.springframework.boot.SpringApplication.run(SpringApplication.java:1352)
pgx_fhir_server        |        at ca.uhn.fhir.jpa.starter.Application.main(Application.java:46)
pgx_fhir_server        |        at java.base/jdk.internal.reflect.NativeMethodAccessorImpl.invoke0(Native Method)
pgx_fhir_server        |        at java.base/jdk.internal.reflect.NativeMethodAccessorImpl.invoke(NativeMethodAccessorImpl.java:77)
pgx_fhir_server        |        at java.base/jdk.internal.reflect.DelegatingMethodAccessorImpl.invoke(DelegatingMethodAccessorImpl.java:43)
pgx_fhir_server        |        at java.base/java.lang.reflect.Method.invoke(Method.java:569)
pgx_fhir_server        |        at org.springframework.boot.loader.MainMethodRunner.run(MainMethodRunner.java:49)
pgx_fhir_server        |        at org.springframework.boot.loader.Launcher.launch(Launcher.java:95)
pgx_fhir_server        |        at org.springframework.boot.loader.Launcher.launch(Launcher.java:58)
pgx_fhir_server        |        at org.springframework.boot.loader.PropertiesLauncher.main(PropertiesLauncher.java:466)
pgx_fhir_server        | Caused by: org.springframework.boot.web.server.WebServerException: Unable to start embedded Tomcat
pgx_fhir_server        |        at org.springframework.boot.web.embedded.tomcat.TomcatWebServer.initialize(TomcatWebServer.java:147)
pgx_fhir_server        |        at org.springframework.boot.web.embedded.tomcat.TomcatWebServer.<init>(TomcatWebServer.java:107)
pgx_fhir_server        |        at org.springframework.boot.web.embedded.tomcat.TomcatServletWebServerFactory.getTomcatWebServer(TomcatServletWebServerFactory.java:516)
pgx_fhir_server        |        at org.springframework.boot.web.embedded.tomcat.TomcatServletWebServerFactory.getWebServer(TomcatServletWebServerFactory.java:222)
pgx_fhir_server        |        at org.springframework.boot.web.servlet.context.ServletWebServerApplicationContext.createWebServer(ServletWebServerApplicationContext.java:188)
pgx_fhir_server        |        at org.springframework.boot.web.servlet.context.ServletWebServerApplicationContext.onRefresh(ServletWebServerApplicationContext.java:162)
pgx_fhir_server        |        ... 16 common frames omitted
pgx_fhir_server        | Caused by: org.springframework.beans.factory.UnsatisfiedDependencyException: Error creating bean with name 'hapiServletRegistration' defined in ca.uhn.fhir.jpa.starter.Application: Unsatisfied dependency expressed through method 'hapiServletRegistration' parameter 0: Error creating bean with name 'restfulServer' defined in class path resource [ca/uhn/fhir/jpa/starter/common/StarterJpaConfig.class]: Unsatisfied dependency expressed through method 'restfulServer' parameter 0: Error creating bean with name 'mySystemDaoR4': Injection of persistence dependencies failed
pgx_fhir_server        |        at org.springframework.beans.factory.support.ConstructorResolver.createArgumentArray(ConstructorResolver.java:795)
pgx_fhir_server        |        at org.springframework.beans.factory.support.ConstructorResolver.instantiateUsingFactoryMethod(ConstructorResolver.java:542)
pgx_fhir_server        |        at org.springframework.beans.factory.support.AbstractAutowireCapableBeanFactory.instantiateUsingFactoryMethod(AbstractAutowireCapableBeanFactory.java:1355)
pgx_fhir_server        |        at org.springframework.beans.factory.support.AbstractAutowireCapableBeanFactory.createBeanInstance(AbstractAutowireCapableBeanFactory.java:1185)
pgx_fhir_server        |        at org.springframework.beans.factory.support.AbstractAutowireCapableBeanFactory.doCreateBean(AbstractAutowireCapableBeanFactory.java:562)
pgx_fhir_server        |        at org.springframework.beans.factory.support.AbstractAutowireCapableBeanFactory.createBean(AbstractAutowireCapableBeanFactory.java:522)
pgx_fhir_server        |        at org.springframework.beans.factory.support.AbstractBeanFactory.lambda$doGetBean$0(AbstractBeanFactory.java:337)
pgx_fhir_server        |        at org.springframework.beans.factory.support.DefaultSingletonBeanRegistry.getSingleton(DefaultSingletonBeanRegistry.java:234)
pgx_fhir_server        |        at org.springframework.beans.factory.support.AbstractBeanFactory.doGetBean(AbstractBeanFactory.java:335)
pgx_fhir_server        |        at org.springframework.beans.factory.support.AbstractBeanFactory.getBean(AbstractBeanFactory.java:205)
pgx_fhir_server        |        at org.springframework.boot.web.servlet.ServletContextInitializerBeans.getOrderedBeansOfType(ServletContextInitializerBeans.java:211)
pgx_fhir_server        |        at org.springframework.boot.web.servlet.ServletContextInitializerBeans.getOrderedBeansOfType(ServletContextInitializerBeans.java:202)
pgx_fhir_server        |        at org.springframework.boot.web.servlet.ServletContextInitializerBeans.addServletContextInitializerBeans(ServletContextInitializerBeans.java:97)
pgx_fhir_server        |        at org.springframework.boot.web.servlet.ServletContextInitializerBeans.<init>(ServletContextInitializerBeans.java:86)
pgx_fhir_server        |        at org.springframework.boot.web.servlet.context.ServletWebServerApplicationContext.getServletContextInitializerBeans(ServletWebServerApplicationContext.java:266)
pgx_fhir_server        |        at org.springframework.boot.web.servlet.context.ServletWebServerApplicationContext.selfInitialize(ServletWebServerApplicationContext.java:240)
pgx_fhir_server        |        at org.springframework.boot.web.embedded.tomcat.TomcatStarter.onStartup(TomcatStarter.java:52)
pgx_fhir_server        |        at org.apache.catalina.core.StandardContext.startInternal(StandardContext.java:4412)
pgx_fhir_server        |        at org.apache.catalina.util.LifecycleBase.start(LifecycleBase.java:164)
pgx_fhir_server        |        at org.apache.catalina.core.ContainerBase$StartChild.call(ContainerBase.java:1203)
pgx_fhir_server        |        at org.apache.catalina.core.ContainerBase$StartChild.call(ContainerBase.java:1193)
pgx_fhir_server        |        at java.base/java.util.concurrent.FutureTask.run(FutureTask.java:264)
pgx_fhir_server        |        at org.apache.tomcat.util.threads.InlineExecutorService.execute(InlineExecutorService.java:75)
pgx_fhir_server        |        at java.base/java.util.concurrent.AbstractExecutorService.submit(AbstractExecutorService.java:145)
pgx_fhir_server        |        at org.apache.catalina.core.ContainerBase.startInternal(ContainerBase.java:749)
pgx_fhir_server        |        at org.apache.catalina.core.StandardHost.startInternal(StandardHost.java:772)
pgx_fhir_server        |        at org.apache.catalina.util.LifecycleBase.start(LifecycleBase.java:164)
pgx_fhir_server        |        at org.apache.catalina.core.ContainerBase$StartChild.call(ContainerBase.java:1203)
pgx_fhir_server        |        at org.apache.catalina.core.ContainerBase$StartChild.call(ContainerBase.java:1193)
pgx_fhir_server        |        at java.base/java.util.concurrent.FutureTask.run(FutureTask.java:264)
pgx_fhir_server        |        at org.apache.tomcat.util.threads.InlineExecutorService.execute(InlineExecutorService.java:75)
pgx_fhir_server        |        at java.base/java.util.concurrent.AbstractExecutorService.submit(AbstractExecutorService.java:145)
pgx_fhir_server        |        at org.apache.catalina.core.ContainerBase.startInternal(ContainerBase.java:749)
pgx_fhir_server        |        at org.apache.catalina.core.StandardEngine.startInternal(StandardEngine.java:203)
pgx_fhir_server        |        at org.apache.catalina.util.LifecycleBase.start(LifecycleBase.java:164)
pgx_fhir_server        |        at org.apache.catalina.core.StandardService.startInternal(StandardService.java:415)
pgx_fhir_server        |        at org.apache.catalina.util.LifecycleBase.start(LifecycleBase.java:164)
pgx_fhir_server        |        at org.apache.catalina.core.StandardServer.startInternal(StandardServer.java:870)
pgx_fhir_server        |        at org.apache.catalina.util.LifecycleBase.start(LifecycleBase.java:164)
pgx_fhir_server        |        at org.apache.catalina.startup.Tomcat.start(Tomcat.java:437)
pgx_fhir_server        |        at org.springframework.boot.web.embedded.tomcat.TomcatWebServer.initialize(TomcatWebServer.java:128)
pgx_fhir_server        |        ... 21 common frames omitted
pgx_fhir_server        | Caused by: org.springframework.beans.factory.UnsatisfiedDependencyException: Error creating bean with name 'restfulServer' defined in class path resource [ca/uhn/fhir/jpa/starter/common/StarterJpaConfig.class]: Unsatisfied dependency expressed through method 'restfulServer' parameter 0: Error creating bean with name 'mySystemDaoR4': Injection of persistence dependencies failed
pgx_fhir_server        |        at org.springframework.beans.factory.support.ConstructorResolver.createArgumentArray(ConstructorResolver.java:795)
pgx_fhir_server        |        at org.springframework.beans.factory.support.ConstructorResolver.instantiateUsingFactoryMethod(ConstructorResolver.java:542)
pgx_fhir_server        |        at org.springframework.beans.factory.support.AbstractAutowireCapableBeanFactory.instantiateUsingFactoryMethod(AbstractAutowireCapableBeanFactory.java:1355)
pgx_fhir_server        |        at org.springframework.beans.factory.support.AbstractAutowireCapableBeanFactory.createBeanInstance(AbstractAutowireCapableBeanFactory.java:1185)
pgx_fhir_server        |        at org.springframework.beans.factory.support.AbstractAutowireCapableBeanFactory.doCreateBean(AbstractAutowireCapableBeanFactory.java:562)
pgx_fhir_server        |        at org.springframework.beans.factory.support.AbstractAutowireCapableBeanFactory.createBean(AbstractAutowireCapableBeanFactory.java:522)
pgx_fhir_server        |        at org.springframework.beans.factory.support.AbstractBeanFactory.lambda$doGetBean$0(AbstractBeanFactory.java:337)
pgx_fhir_server        |        at org.springframework.beans.factory.support.DefaultSingletonBeanRegistry.getSingleton(DefaultSingletonBeanRegistry.java:234)
pgx_fhir_server        |        at org.springframework.beans.factory.support.AbstractBeanFactory.doGetBean(AbstractBeanFactory.java:335)
pgx_fhir_server        |        at org.springframework.beans.factory.support.AbstractBeanFactory.getBean(AbstractBeanFactory.java:200)
pgx_fhir_server        |        at org.springframework.beans.factory.config.DependencyDescriptor.resolveCandidate(DependencyDescriptor.java:254)
pgx_fhir_server        |        at org.springframework.beans.factory.support.DefaultListableBeanFactory.doResolveDependency(DefaultListableBeanFactory.java:1443)
pgx_fhir_server        |        at org.springframework.beans.factory.support.DefaultListableBeanFactory.resolveDependency(DefaultListableBeanFactory.java:1353)
pgx_fhir_server        |        at org.springframework.beans.factory.support.ConstructorResolver.resolveAutowiredArgument(ConstructorResolver.java:904)
pgx_fhir_server        |        at org.springframework.beans.factory.support.ConstructorResolver.createArgumentArray(ConstructorResolver.java:782)
pgx_fhir_server        |        ... 61 common frames omitted
pgx_fhir_server        | Caused by: org.springframework.beans.factory.BeanCreationException: Error creating bean with name 'mySystemDaoR4': Injection of persistence dependencies failed
pgx_fhir_server        |        at org.springframework.orm.jpa.support.PersistenceAnnotationBeanPostProcessor.postProcessProperties(PersistenceAnnotationBeanPostProcessor.java:388)
pgx_fhir_server        |        at org.springframework.beans.factory.support.AbstractAutowireCapableBeanFactory.populateBean(AbstractAutowireCapableBeanFactory.java:1439)
pgx_fhir_server        |        at org.springframework.beans.factory.support.AbstractAutowireCapableBeanFactory.doCreateBean(AbstractAutowireCapableBeanFactory.java:599)
pgx_fhir_server        |        at org.springframework.beans.factory.support.AbstractAutowireCapableBeanFactory.createBean(AbstractAutowireCapableBeanFactory.java:522)
pgx_fhir_server        |        at org.springframework.beans.factory.support.AbstractBeanFactory.lambda$doGetBean$0(AbstractBeanFactory.java:337)
pgx_fhir_server        |        at org.springframework.beans.factory.support.DefaultSingletonBeanRegistry.getSingleton(DefaultSingletonBeanRegistry.java:234)
pgx_fhir_server        |        at org.springframework.beans.factory.support.AbstractBeanFactory.doGetBean(AbstractBeanFactory.java:335)
pgx_fhir_server        |        at org.springframework.beans.factory.support.AbstractBeanFactory.getBean(AbstractBeanFactory.java:200)
pgx_fhir_server        |        at org.springframework.beans.factory.config.DependencyDescriptor.resolveCandidate(DependencyDescriptor.java:254)
pgx_fhir_server        |        at org.springframework.beans.factory.support.DefaultListableBeanFactory.doResolveDependency(DefaultListableBeanFactory.java:1443)
pgx_fhir_server        |        at org.springframework.beans.factory.support.DefaultListableBeanFactory.resolveDependency(DefaultListableBeanFactory.java:1353)
pgx_fhir_server        |        at org.springframework.beans.factory.support.ConstructorResolver.resolveAutowiredArgument(ConstructorResolver.java:904)
pgx_fhir_server        |        at org.springframework.beans.factory.support.ConstructorResolver.createArgumentArray(ConstructorResolver.java:782)
pgx_fhir_server        |        ... 75 common frames omitted
pgx_fhir_server        | Caused by: org.springframework.beans.factory.BeanCreationException: Error creating bean with name 'entityManagerFactory' defined in class path resource [ca/uhn/fhir/jpa/starter/common/StarterJpaConfig.class]: Unable to create requested service [org.hibernate.engine.jdbc.env.spi.JdbcEnvironment] due to: Unable to resolve name [ca.uhn.fhir.jpa.model.dialect.HapiFhirPostgres95Dialect] as strategy [org.hibernate.dialect.Dialect]
pgx_fhir_server        |        at org.springframework.beans.factory.support.AbstractAutowireCapableBeanFactory.initializeBean(AbstractAutowireCapableBeanFactory.java:1806)
pgx_fhir_server        |        at org.springframework.beans.factory.support.AbstractAutowireCapableBeanFactory.doCreateBean(AbstractAutowireCapableBeanFactory.java:600)
pgx_fhir_server        |        at org.springframework.beans.factory.support.AbstractAutowireCapableBeanFactory.createBean(AbstractAutowireCapableBeanFactory.java:522)
pgx_fhir_server        |        at org.springframework.beans.factory.support.AbstractBeanFactory.lambda$doGetBean$0(AbstractBeanFactory.java:337)
pgx_fhir_server        |        at org.springframework.beans.factory.support.DefaultSingletonBeanRegistry.getSingleton(DefaultSingletonBeanRegistry.java:234)
pgx_fhir_server        |        at org.springframework.beans.factory.support.AbstractBeanFactory.doGetBean(AbstractBeanFactory.java:335)
pgx_fhir_server        |        at org.springframework.beans.factory.support.AbstractBeanFactory.getBean(AbstractBeanFactory.java:225)
pgx_fhir_server        |        at org.springframework.beans.factory.support.DefaultListableBeanFactory.resolveNamedBean(DefaultListableBeanFactory.java:1323)
pgx_fhir_server        |        at org.springframework.beans.factory.support.DefaultListableBeanFactory.resolveNamedBean(DefaultListableBeanFactory.java:1284)
pgx_fhir_server        |        at org.springframework.beans.factory.support.DefaultListableBeanFactory.resolveNamedBean(DefaultListableBeanFactory.java:1252)
pgx_fhir_server        |        at org.springframework.orm.jpa.support.PersistenceAnnotationBeanPostProcessor.findDefaultEntityManagerFactory(PersistenceAnnotationBeanPostProcessor.java:593)
pgx_fhir_server        |        at org.springframework.orm.jpa.support.PersistenceAnnotationBeanPostProcessor.findEntityManagerFactory(PersistenceAnnotationBeanPostProcessor.java:557)
pgx_fhir_server        |        at org.springframework.orm.jpa.support.PersistenceAnnotationBeanPostProcessor$PersistenceElement.resolveEntityManager(PersistenceAnnotationBeanPostProcessor.java:724)
pgx_fhir_server        |        at org.springframework.orm.jpa.support.PersistenceAnnotationBeanPostProcessor$PersistenceElement.getResourceToInject(PersistenceAnnotationBeanPostProcessor.java:697)
pgx_fhir_server        |        at org.springframework.beans.factory.annotation.InjectionMetadata$InjectedElement.inject(InjectionMetadata.java:270)
pgx_fhir_server        |        at org.springframework.beans.factory.annotation.InjectionMetadata.inject(InjectionMetadata.java:145)
pgx_fhir_server        |        at org.springframework.orm.jpa.support.PersistenceAnnotationBeanPostProcessor.postProcessProperties(PersistenceAnnotationBeanPostProcessor.java:385)
pgx_fhir_server        |        ... 87 common frames omitted
pgx_fhir_server        | Caused by: org.hibernate.service.spi.ServiceException: Unable to create requested service [org.hibernate.engine.jdbc.env.spi.JdbcEnvironment] due to: Unable to resolve name [ca.uhn.fhir.jpa.model.dialect.HapiFhirPostgres95Dialect] as strategy [org.hibernate.dialect.Dialect]
pgx_fhir_server        |        at org.hibernate.service.internal.AbstractServiceRegistryImpl.createService(AbstractServiceRegistryImpl.java:276)
pgx_fhir_server        |        at org.hibernate.service.internal.AbstractServiceRegistryImpl.initializeService(AbstractServiceRegistryImpl.java:238)
pgx_fhir_server        |        at org.hibernate.service.internal.AbstractServiceRegistryImpl.getService(AbstractServiceRegistryImpl.java:215)
pgx_fhir_server        |        at org.hibernate.boot.model.relational.Database.<init>(Database.java:45)
pgx_fhir_server        |        at org.hibernate.boot.internal.InFlightMetadataCollectorImpl.getDatabase(InFlightMetadataCollectorImpl.java:226)
pgx_fhir_server        |        at org.hibernate.boot.internal.InFlightMetadataCollectorImpl.<init>(InFlightMetadataCollectorImpl.java:194)
pgx_fhir_server        |        at org.hibernate.boot.model.process.spi.MetadataBuildingProcess.complete(MetadataBuildingProcess.java:171)
pgx_fhir_server        |        at org.hibernate.jpa.boot.internal.EntityManagerFactoryBuilderImpl.metadata(EntityManagerFactoryBuilderImpl.java:1431)
pgx_fhir_server        |        at org.hibernate.jpa.boot.internal.EntityManagerFactoryBuilderImpl.build(EntityManagerFactoryBuilderImpl.java:1502)
pgx_fhir_server        |        at org.hibernate.jpa.HibernatePersistenceProvider.createContainerEntityManagerFactory(HibernatePersistenceProvider.java:142)
pgx_fhir_server        |        at org.springframework.orm.jpa.LocalContainerEntityManagerFactoryBean.createNativeEntityManagerFactory(LocalContainerEntityManagerFactoryBean.java:390)
pgx_fhir_server        |        at org.springframework.orm.jpa.AbstractEntityManagerFactoryBean.buildNativeEntityManagerFactory(AbstractEntityManagerFactoryBean.java:409)
pgx_fhir_server        |        at org.springframework.orm.jpa.AbstractEntityManagerFactoryBean.afterPropertiesSet(AbstractEntityManagerFactoryBean.java:396)
pgx_fhir_server        |        at org.springframework.orm.jpa.LocalContainerEntityManagerFactoryBean.afterPropertiesSet(LocalContainerEntityManagerFactoryBean.java:366)
pgx_fhir_server        |        at org.springframework.beans.factory.support.AbstractAutowireCapableBeanFactory.invokeInitMethods(AbstractAutowireCapableBeanFactory.java:1853)
pgx_fhir_server        |        at org.springframework.beans.factory.support.AbstractAutowireCapableBeanFactory.initializeBean(AbstractAutowireCapableBeanFactory.java:1802)
pgx_fhir_server        |        ... 103 common frames omitted
pgx_fhir_server        | Caused by: org.hibernate.boot.registry.selector.spi.StrategySelectionException: Unable to resolve name [ca.uhn.fhir.jpa.model.dialect.HapiFhirPostgres95Dialect] as strategy [org.hibernate.dialect.Dialect]
pgx_fhir_server        |        at org.hibernate.boot.registry.selector.internal.StrategySelectorImpl.selectStrategyImplementor(StrategySelectorImpl.java:154)
pgx_fhir_server        |        at org.hibernate.boot.registry.selector.internal.StrategySelectorImpl.resolveStrategy(StrategySelectorImpl.java:236)
pgx_fhir_server        |        at org.hibernate.boot.registry.selector.internal.StrategySelectorImpl.resolveStrategy(StrategySelectorImpl.java:189)
pgx_fhir_server        |        at org.hibernate.engine.jdbc.dialect.internal.DialectFactoryImpl.constructDialect(DialectFactoryImpl.java:123)
pgx_fhir_server        |        at org.hibernate.engine.jdbc.dialect.internal.DialectFactoryImpl.buildDialect(DialectFactoryImpl.java:88)
pgx_fhir_server        |        at org.hibernate.engine.jdbc.env.internal.JdbcEnvironmentInitiator.getJdbcEnvironmentWithDefaults(JdbcEnvironmentInitiator.java:181)
pgx_fhir_server        |        at org.hibernate.engine.jdbc.env.internal.JdbcEnvironmentInitiator.getJdbcEnvironmentUsingJdbcMetadata(JdbcEnvironmentInitiator.java:392)
pgx_fhir_server        |        at org.hibernate.engine.jdbc.env.internal.JdbcEnvironmentInitiator.initiateService(JdbcEnvironmentInitiator.java:129)
pgx_fhir_server        |        at org.hibernate.engine.jdbc.env.internal.JdbcEnvironmentInitiator.initiateService(JdbcEnvironmentInitiator.java:81)
pgx_fhir_server        |        at org.hibernate.boot.registry.internal.StandardServiceRegistryImpl.initiateService(StandardServiceRegistryImpl.java:130)
pgx_fhir_server        |        at org.hibernate.service.internal.AbstractServiceRegistryImpl.createService(AbstractServiceRegistryImpl.java:263)
pgx_fhir_server        |        ... 118 common frames omitted
pgx_fhir_server        | Caused by: org.hibernate.boot.registry.classloading.spi.ClassLoadingException: Unable to load class [ca.uhn.fhir.jpa.model.dialect.HapiFhirPostgres95Dialect]
pgx_fhir_server        |        at org.hibernate.boot.registry.classloading.internal.ClassLoaderServiceImpl.classForName(ClassLoaderServiceImpl.java:126)
pgx_fhir_server        |        at org.hibernate.boot.registry.selector.internal.StrategySelectorImpl.selectStrategyImplementor(StrategySelectorImpl.java:150)
pgx_fhir_server        |        ... 128 common frames omitted
pgx_fhir_server        | Caused by: java.lang.ClassNotFoundException: Could not load requested class : ca.uhn.fhir.jpa.model.dialect.HapiFhirPostgres95Dialect
pgx_fhir_server        |        at org.hibernate.boot.registry.classloading.internal.AggregatedClassLoader.findClass(AggregatedClassLoader.java:216)
pgx_fhir_server        |        at java.base/java.lang.ClassLoader.loadClass(ClassLoader.java:592)
pgx_fhir_server        |        at java.base/java.lang.ClassLoader.loadClass(ClassLoader.java:525)
pgx_fhir_server        |        at java.base/java.lang.Class.forName0(Native Method)
pgx_fhir_server        |        at java.base/java.lang.Class.forName(Class.java:467)
pgx_fhir_server        |        at org.hibernate.boot.registry.classloading.internal.ClassLoaderServiceImpl.classForName(ClassLoaderServiceImpl.java:123)
pgx_fhir_server        |        ... 129 common frames omitted
pgx_fhir_server        | Caused by: java.lang.Throwable: null
pgx_fhir_server        |        at org.hibernate.boot.registry.classloading.internal.AggregatedClassLoader.findClass(AggregatedClassLoader.java:209)
pgx_fhir_server        |        ... 134 common frames omitted
pgx_fhir_server        |        Suppressed: java.lang.ClassNotFoundException: ca.uhn.fhir.jpa.model.dialect.HapiFhirPostgres95Dialect
pgx_fhir_server        |                at org.springframework.boot.web.embedded.tomcat.TomcatEmbeddedWebappClassLoader.loadClass(TomcatEmbeddedWebappClassLoader.java:72)
pgx_fhir_server        |                at org.apache.catalina.loader.WebappClassLoaderBase.loadClass(WebappClassLoaderBase.java:1144)
pgx_fhir_server        |                at org.hibernate.boot.registry.classloading.internal.AggregatedClassLoader.findClass(AggregatedClassLoader.java:206)
pgx_fhir_server        |                ... 134 common frames omitted
pgx_fhir_server        |        Suppressed: java.lang.ClassNotFoundException: ca.uhn.fhir.jpa.model.dialect.HapiFhirPostgres95Dialect
pgx_fhir_server        |                at java.base/java.net.URLClassLoader.findClass(URLClassLoader.java:445)
pgx_fhir_server        |                at java.base/java.lang.ClassLoader.loadClass(ClassLoader.java:592)
pgx_fhir_server        |                at org.springframework.boot.loader.LaunchedURLClassLoader.loadClass(LaunchedURLClassLoader.java:150)
pgx_fhir_server        |                at java.base/java.lang.ClassLoader.loadClass(ClassLoader.java:525)
pgx_fhir_server        |                at org.hibernate.boot.registry.classloading.internal.AggregatedClassLoader.findClass(AggregatedClassLoader.java:206)
pgx_fhir_server        |                ... 134 common frames omitted
pgx_fhir_server        |        Suppressed: java.lang.ClassNotFoundException: ca.uhn.fhir.jpa.model.dialect.HapiFhirPostgres95Dialect
pgx_fhir_server        |                at org.springframework.boot.web.embedded.tomcat.TomcatEmbeddedWebappClassLoader.loadClass(TomcatEmbeddedWebappClassLoader.java:72)
pgx_fhir_server        |                at org.apache.catalina.loader.WebappClassLoaderBase.loadClass(WebappClassLoaderBase.java:1144)
pgx_fhir_server        |                at org.hibernate.boot.registry.classloading.internal.AggregatedClassLoader.findClass(AggregatedClassLoader.java:206)
pgx_fhir_server        |                ... 134 common frames omitted
pgx_fhir_server        |        Suppressed: java.lang.ClassNotFoundException: ca.uhn.fhir.jpa.model.dialect.HapiFhirPostgres95Dialect
pgx_fhir_server        |                at java.base/jdk.internal.loader.BuiltinClassLoader.loadClass(BuiltinClassLoader.java:641)
pgx_fhir_server        |                at java.base/jdk.internal.loader.ClassLoaders$AppClassLoader.loadClass(ClassLoaders.java:188)
pgx_fhir_server        |                at java.base/java.lang.ClassLoader.loadClass(ClassLoader.java:525)
pgx_fhir_server        |                at org.hibernate.boot.registry.classloading.internal.AggregatedClassLoader.findClass(AggregatedClassLoader.java:206)
pgx_fhir_server        |                ... 134 common frames omitted
pgx_fhir_server        |
pgx_fhir_server exited with code 0